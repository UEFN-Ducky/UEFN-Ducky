"""Chats and duckies belong to the Ducky account signed in on this PC (migration 0024).

Every row of ``conversations``, ``messages``, ``folders``, ``duckies`` and
``duckies_history`` carries its owner's ``account_id`` (``scopes.account_id()``,
``_local`` signed out), and the repos read and write only the current account's rows:
signing in to another account shows that account's chats and duckies, and signing out
shows the ``_local`` ones. Shipped duckies are not rows; they are there for everyone.

What the person wrote is sealed for its account with :mod:`backend.uefn_plugins.data_crypto`
(DPAPI plus the account data key; plain DPAPI signed out): chat titles and state, message
bodies and payloads, folder names, ducky data, other Duckies' chat copies. Another account
on this PC can't read them, not even from ``ducky.db``. No key yet raises
``data_crypto.Locked``; nothing is written as plaintext.

Search works without the words: a message's search text is stored as one keyed token per
word (:func:`blind_text`), and a query becomes the same tokens (:func:`blind_query`).

Rows from before (account ``''``) go whole to the first account that opens them
(:func:`claim`, from ``scopes.account_id``) and are sealed in place (:func:`seal_pending`,
at boot and at sign-in), with what else held them in plaintext: the copies of old rows
that account already had (parked), the duckies settings kept from before they had their
own table (moved, sealed, into ``meta``), plugin search records
(:mod:`backend.search.sealed_records`). Nothing is deleted. The space those rewrites free
is zeroed, and once nothing of the account is left in plaintext:

- ducky.db is rebuilt at the next start (``db.request_vacuum``), dropping old fragments
  from free pages and page slack;
- its backups from before (snapshots, files a restore replaced, corrupt copies) are sealed
  for the account the rows went to (``sealed_files.seal_backups``).

Chat attachments on disk are sealed for their owner while another account is signed in,
and opened when the owner is back (``sealed_files.arrange_chat_files``).
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import logging
import os
import re
import threading
import time
import unicodedata
from collections import OrderedDict
from collections.abc import Iterator

from backend.store import db

UNCLAIMED = ""
# Owner of an old row the claiming account already had a copy of (the same folder or ducky):
# the account keeps its own, the old copy stays in the table under an owner nobody reads.
PARKED = "_parked:"
_KEY_META = "chat_key:"
_CLAIMED_BY = "rows_claimed_by"  # the account the rows from before 0024 went to
_SEALED_AT = "rows_sealed_at"  # when nothing of theirs was left in plaintext
_VACUUM_WANTED = "vacuum_wanted"  # a seal pass rewrote plaintext: rebuild once it's done
_DONE = "rows_sealed:"  # <account>: nothing of it was left in plaintext (checks skipped after)
_LEGACY_SETTINGS = "legacy_duckies_settings:"
_SEALED = "enc1:"  # data_crypto.PREFIX
_SEALED_BLOB = b"UDE1"  # data_crypto.FILE_MAGIC

# Columns holding what the person wrote, sealed in place by seal_pending (messages and
# their payloads are the chats repo's own: seal_stored_messages).
SEALED_COLUMNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("conversations", ("title", "state")),
    ("folders", ("name",)),
    ("duckies", ("data",)),
    ("duckies_history", ("data",)),
    ("ducky_mirror_chats", ("title", "rows")),
)

_log = logging.getLogger("uefn_ducky.store.account_rows")
_lock = threading.Lock()
_chat_keys: dict[tuple[str, str], bytes] = {}
_sealing: set[tuple[str, str]] = set()

# Opened titles, chat state and folder names: a sidebar lists every chat of a project on
# each change, two DPAPI calls a chat without this.
_OPENED_MAX = 4096
_OPENED_MAX_CHARS = 16 * 1024 * 1024
_OPENED_ITEM_MAX = 256 * 1024
_opened: OrderedDict[tuple[str, str], str] = OrderedDict()
_opened_chars = 0


def current() -> str:
    """The signed-in account's id (``_local`` signed out); claims rows from before 0024."""
    from backend.uefn_plugins.scopes import account_id

    return account_id()


# --------------------------------------------------------------------------- sealing


def ready(account: str) -> bytes:
    """Before a write transaction: the account's data key is on this PC (else ``Locked``)
    and its chat key is loaded, so sealing inside the transaction needs no network call
    and no other write. Returns the chat key."""
    from backend.uefn_plugins import data_crypto

    if not data_crypto.available(account):
        raise data_crypto.Locked("Waiting for this account's data key. Your chats open once you're online.")
    key = chat_key(account)
    _ensure_file_keys(account)
    return key


_file_keys_done: set[tuple[str, str]] = set()
# A failed try waits this long: retried on every call, it read the keys file on every
# chat save and checkpoint.
FILE_KEYS_RETRY_S = 60.0
_file_keys_retry: dict[tuple[str, str], float] = {}


def _ensure_file_keys(account: str) -> None:
    """The account's file keypair, as soon as its data key is here (once per process), so
    its chat attachments can be sealed whenever another account signs in on this PC."""
    memo = (str(db.db_path()), account)
    if memo in _file_keys_done or _file_keys_retry.get(memo, 0.0) > time.monotonic():
        return
    if db.connect().in_transaction:
        return
    try:
        from backend.store import sealed_files

        sealed_files.ensure_keypair(account)
        _file_keys_done.add(memo)
    except Exception:  # noqa: BLE001 — never in the way of a read or write; tried again later
        _file_keys_retry[memo] = time.monotonic() + FILE_KEYS_RETRY_S
        _log.debug("file keys for this account not made yet", exc_info=True)


def chat_key(account: str) -> bytes:
    """The account's key for search tokens and payload names: random, stored sealed."""
    from backend.uefn_plugins import data_crypto

    memo = (str(db.db_path()), account)
    with _lock:
        key = _chat_keys.get(memo)
    if key is not None:
        return key
    conn = db.connect()
    name = _KEY_META + account
    row = conn.execute("SELECT value FROM meta WHERE key=?", (name,)).fetchone()
    if row is None:
        fresh = data_crypto.seal_text(base64.b64encode(os.urandom(32)).decode("ascii"), account)
        with db.write_txn(conn):
            conn.execute(
                "INSERT OR IGNORE INTO meta(key, value, updated) VALUES (?, ?, ?)", (name, fresh, time.time())
            )
        row = conn.execute("SELECT value FROM meta WHERE key=?", (name,)).fetchone()
    key = base64.b64decode(data_crypto.open_text(str(row[0]), account))
    with _lock:
        _chat_keys[memo] = key
    return key


def is_sealed(stored: object) -> bool:
    return isinstance(stored, str) and stored.startswith(_SEALED)


def seal(text: str, account: str) -> str:
    """``text`` sealed for ``account``; an empty value stays empty (nothing to hide)."""
    from backend.uefn_plugins import data_crypto

    return data_crypto.seal_text(text, account) if text else ""


def open_text(stored: str, account: str) -> str:
    """Plaintext of a stored value: sealed ones are opened, rows from before pass as they are."""
    from backend.uefn_plugins import data_crypto

    return data_crypto.open_text(stored, account) if is_sealed(stored) else stored


def open_cached(stored: str, account: str) -> str:
    """:func:`open_text` for small values read over and over (titles, chat state, names)."""
    global _opened_chars
    if not is_sealed(stored) or len(stored) > _OPENED_ITEM_MAX:
        return open_text(stored, account)
    memo = (account, stored)
    with _lock:
        hit = _opened.get(memo)
        if hit is not None:
            _opened.move_to_end(memo)
            return hit
    text = open_text(stored, account)
    with _lock:
        if memo not in _opened:
            _opened[memo] = text
            _opened_chars += len(text)
        while _opened and (len(_opened) > _OPENED_MAX or _opened_chars > _OPENED_MAX_CHARS):
            _, dropped = _opened.popitem(last=False)
            _opened_chars -= len(dropped)
    return text


def seal_blob(data: bytes, account: str) -> bytes:
    from backend.uefn_plugins import data_crypto

    return data_crypto.seal_bytes(data, account)


def open_blob(raw: bytes, account: str) -> bytes:
    """A stored payload: sealed ones are opened, payloads from before pass as they are."""
    from backend.uefn_plugins import data_crypto

    return data_crypto.open_bytes(raw, account) if raw.startswith(_SEALED_BLOB) else raw


def forget() -> None:
    """Login funnel (``data_crypto.on_login_change``): opened values and keys go."""
    global _opened_chars
    from backend.store import sealed_files

    with _lock:
        _chat_keys.clear()
        _opened.clear()
        _opened_chars = 0
    _file_keys_done.clear()
    _file_keys_retry.clear()
    sealed_files.forget()
    from backend.agent.secrets import forget_login_stamp

    forget_login_stamp()


# --------------------------------------------------------------------------- search without the words

_WORD = re.compile(r"[^\W_]+")


def words(text: str) -> list[str]:
    """The words search matches on: case and accents folded, ``_`` and punctuation split."""
    folded = unicodedata.normalize("NFKD", text.casefold())
    return _WORD.findall("".join(ch for ch in folded if not unicodedata.combining(ch)))


def _token(word: str, key: bytes) -> str:
    digest = hashlib.blake2b(word.encode("utf-8"), key=key, digest_size=5, person=b"chat-word").digest()
    return base64.b32encode(digest).decode("ascii").lower()


def blind_text(text: str, key: bytes) -> str:
    """What the search index keeps of ``text``: one keyed token per word, in order."""
    seen: dict[str, str] = {}
    out: list[str] = []
    for word in words(text):
        token = seen.get(word)
        if token is None:
            token = seen[word] = _token(word, key)
        out.append(token)
    return " ".join(out)


def blind_query(query: str, key: bytes) -> str:
    """FTS5 query over :func:`blind_text` rows: every term as a phrase, or all as one."""
    terms = [ws for ws in (words(t) for t in query.split()) if ws]
    if not terms:
        return ""
    phrases = " ".join('"' + " ".join(_token(w, key) for w in ws) + '"' for ws in terms)
    if len(terms) > 1:
        joined = " ".join(_token(w, key) for ws in terms for w in ws)
        return f'({phrases}) OR "{joined}"'
    return phrases


def part_digest(text: str, key: bytes) -> str:
    """Name of a stored payload: keyed, so two accounts never share one."""
    return hashlib.blake2b(text.encode("utf-8"), key=key, digest_size=32, person=b"chat-part").hexdigest()


# --------------------------------------------------------------------------- rows from before

_UNCLAIMED_ROWS = (
    "SELECT EXISTS(SELECT 1 FROM conversations WHERE account_id='') "
    "OR EXISTS(SELECT 1 FROM folders WHERE account_id='') "
    "OR EXISTS(SELECT 1 FROM duckies WHERE account_id='') "
    "OR EXISTS(SELECT 1 FROM duckies_history WHERE account_id='')"
)


@contextlib.contextmanager
def _zeroing(conn) -> Iterator[None]:
    """Space a rewrite frees is zeroed, so a value's old plaintext doesn't stay in the file."""
    before = int(conn.execute("PRAGMA secure_delete").fetchone()[0])
    conn.execute("PRAGMA secure_delete=ON")
    try:
        yield
    finally:
        conn.execute(f"PRAGMA secure_delete={before}")


def claim(account: str) -> int:
    """Rows from before 0024 become ``account``'s, all of them at once. Returns how many.
    If the database already names the account they went to (a backup sealed for it,
    restored while another account was signed in), they go to that account instead.

    A message belongs to its chat; its own ``account_id`` is filled when it is sealed
    (a batch at a time), not here: rewriting every message in one transaction would hold
    the database for as long as copying it."""
    conn = db.connect()
    if not conn.execute(_UNCLAIMED_ROWS).fetchone()[0]:
        return 0
    account = claimer() or account
    with _zeroing(conn), db.write_txn(conn):
        moved = conn.execute("UPDATE conversations SET account_id=? WHERE account_id=''", (account,)).rowcount
        for table in ("folders", "duckies"):
            moved += conn.execute(f"UPDATE OR IGNORE {table} SET account_id=? WHERE account_id=''", (account,)).rowcount
            conn.execute(f"UPDATE {table} SET account_id=? || rowid WHERE account_id=''", (PARKED,))
        moved += conn.execute("UPDATE duckies_history SET account_id=? WHERE account_id=''", (account,)).rowcount
        conn.execute(
            "INSERT OR IGNORE INTO meta(key, value, updated) VALUES (?, ?, ?)", (_CLAIMED_BY, account, time.time())
        )
    return moved


def claimer() -> str:
    """The account the rows from before 0024 went to (``''`` while nobody claimed any)."""
    row = db.connect().execute("SELECT value FROM meta WHERE key=?", (_CLAIMED_BY,)).fetchone()
    return "" if row is None else str(row[0])


def owned_ids(account: str) -> tuple[list[str], list[str]]:
    """(chat ids, folder ids) of one account, for a sidebar dropping them on a switch."""
    conn = db.connect()
    convs = [str(r[0]) for r in conn.execute("SELECT id FROM conversations WHERE account_id=?", (account,))]
    folders = [str(r[0]) for r in conn.execute("SELECT DISTINCT id FROM folders WHERE account_id=?", (account,))]
    return convs, folders


_PARKED_ROWS = "substr(account_id, 1, 8) = '" + PARKED + "'"
_HAS_PARKED = ("folders", "duckies")


def _seal_table(conn, table: str, cols: tuple[str, ...], account: str, batch: int, *, parked: bool = False) -> int:
    """Seal one table's plaintext values of ``account`` in place, a batch per transaction;
    a value changed meanwhile is left to the save that changed it (it wrote it sealed).
    ``parked``: the parked copies instead, sealed for ``account`` (the one that claimed them)."""
    pending = " OR ".join(f"({c} <> '' AND substr({c}, 1, 5) <> ?)" for c in cols)
    sets = ", ".join(f"{c}=?" for c in cols)
    same = " AND ".join(f"{c}=?" for c in cols)
    owner, owner_args = (_PARKED_ROWS, ()) if parked else ("account_id=?", (account,))
    sealed = cursor = 0
    while True:
        rows = conn.execute(
            f"SELECT rowid, {', '.join(cols)} FROM {table} WHERE {owner} AND rowid>? AND ({pending}) "
            "ORDER BY rowid LIMIT ?",
            (*owner_args, cursor, *[_SEALED] * len(cols), batch),
        ).fetchall()
        if not rows:
            return sealed
        todo = [
            (rowid, list(values), [v if not v or is_sealed(v) else seal(str(v), account) for v in values])
            for rowid, *values in rows
        ]
        with db.write_txn(conn):
            for rowid, old, new in todo:
                sealed += conn.execute(f"UPDATE {table} SET {sets} WHERE rowid=? AND {same}", (*new, rowid, *old)).rowcount
        cursor = int(rows[-1][0])


def _set_meta(conn, key: str, value: str, *, replace: bool = True) -> None:
    verb = "INSERT OR REPLACE" if replace else "INSERT OR IGNORE"
    with db.write_txn(conn):
        conn.execute(f"{verb} INTO meta(key, value, updated) VALUES (?, ?, ?)", (key, value, time.time()))


def _get_meta(conn, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return None if row is None else str(row[0])


def _plaintext_left(conn, account: str, *, claimer_pass: bool) -> bool:
    """Anything of ``account`` ducky.db still holds in plaintext (with the parked copies
    and the old duckies settings when it is the account that claimed the rows from before)."""
    from backend.search import sealed_records
    from backend.store.repos.settings import LEGACY_DUCKY_KEYS

    for table, cols in SEALED_COLUMNS:
        pending = " OR ".join(f"({c} <> '' AND substr({c}, 1, 5) <> ?)" for c in cols)
        owner = "account_id=?" + (f" OR {_PARKED_ROWS}" if claimer_pass and table in _HAS_PARKED else "")
        if conn.execute(
            f"SELECT EXISTS(SELECT 1 FROM {table} WHERE ({owner}) AND ({pending}))", (account, *[_SEALED] * len(cols))
        ).fetchone()[0]:
            return True
    if conn.execute(
        "SELECT EXISTS(SELECT 1 FROM messages m JOIN conversations c ON c.id = m.conv_id "
        "WHERE c.account_id=? AND m.body <> '' AND substr(m.body, 1, 5) <> ?)",
        (account, _SEALED),
    ).fetchone()[0]:
        return True
    if conn.execute(
        "SELECT EXISTS(SELECT 1 FROM message_parts p WHERE substr(p.data, 1, 4) <> ? AND EXISTS ("
        "SELECT 1 FROM message_part_refs r JOIN messages m ON m.id = r.message_id "
        "JOIN conversations c ON c.id = m.conv_id WHERE r.hash = p.hash AND c.account_id=?))",
        (_SEALED_BLOB, account),
    ).fetchone()[0]:
        return True
    if sealed_records.plain_left(account):
        return True
    if claimer_pass:
        marks = ", ".join("?" * len(LEGACY_DUCKY_KEYS))
        if conn.execute(f"SELECT EXISTS(SELECT 1 FROM settings WHERE key IN ({marks}))", LEGACY_DUCKY_KEYS).fetchone()[0]:
            return True
    return False


def move_legacy_settings(account: str) -> int:
    """The duckies settings kept from before duckies had their own table (0014; copied into
    it then): kept sealed for ``account`` in ``meta``, the plaintext rows go. Also out of
    the old ``panel_settings.json`` copy. Returns how many rows moved."""
    from backend.store.importers import duckies as duckies_import
    from backend.store.repos import kv
    from backend.store.repos import settings as settings_repo

    duckies_import.ensure()  # they go into the duckies table first, if they never went
    if kv.meta_get("imported:duckies") is None:
        return 0
    conn = db.connect()
    keys = settings_repo.LEGACY_DUCKY_KEYS
    marks = ", ".join("?" * len(keys))
    rows = conn.execute(f"SELECT key, value FROM settings WHERE key IN ({marks})", keys).fetchall()
    moved = 0
    if rows:
        sealed = seal(json.dumps({str(k): str(v) for k, v in rows}, ensure_ascii=False), account)
        name = _LEGACY_SETTINGS + account
        with _zeroing(conn), db.write_txn(conn):
            if _get_meta(conn, name) is not None:
                name = f"{name}:{int(time.time())}"  # moved before; this copy is kept beside it
            conn.execute("INSERT INTO meta(key, value, updated) VALUES (?, ?, ?)", (name, sealed, time.time()))
            moved = conn.execute(f"DELETE FROM settings WHERE key IN ({marks})", keys).rowcount
        settings_repo.forget_snapshot()
    _strip_settings_file(keys)
    return moved


def _strip_settings_file(keys: tuple[str, ...]) -> None:
    """The old ``panel_settings.json`` (the files backend's copy) drops the duckies too."""
    from backend.store.switch import use_db

    path = db.app_root() / "panel_settings.json"
    if not use_db("settings") or not path.is_file():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if not isinstance(data, dict) or not any(k in data for k in keys):
        return
    kept = {k: v for k, v in data.items() if k not in keys}
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(kept, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        _log.warning("could not take the duckies out of panel_settings.json", exc_info=True)
    finally:
        tmp.unlink(missing_ok=True)


class _AlreadySealed(Exception):
    """Nothing of the account was left in plaintext at an earlier pass."""


def seal_pending(account: str | None = None, *, batch: int = 40, pause_s: float = 0.05) -> dict[str, int]:
    """Seal every value of ``account`` still in plaintext (rows from before 0024, copies an
    older build wrote, the account's plugin search records; for the account that claimed
    the rows from before, also their parked copies and the old duckies settings). Then,
    once nothing of it is left in plaintext, ask for the one ducky.db rebuild and seal its
    backups from before. Chat attachments are arranged for the signed-in account.
    Resumable; does nothing while the account is locked. Never raises ``Locked`` (the next
    boot or sign-in tries again)."""
    from backend.search import sealed_records
    from backend.store import sealed_files
    from backend.uefn_plugins import data_crypto

    account = account or current()
    memo = (str(db.db_path()), account)
    with _lock:
        if memo in _sealing:
            return {}
        _sealing.add(memo)
    conn = db.connect()
    out: dict[str, int] = {}
    try:
        out["leftovers_removed"] = sealed_files.sweep_leftovers()
    except Exception:  # noqa: BLE001
        _log.warning("sweeping plain copies left by an interrupted open failed", exc_info=True)
    try:
        ready(account)
        sealed_files.ensure_keypair(account)
        from backend.store.repos import chats

        claimer_pass = (claimer() or account) == account
        # Once nothing of the account is left in plaintext, every writer seals what it
        # writes (an older build can't open this schema): later passes skip the scans.
        if _get_meta(conn, _DONE + account) is not None:
            out.update(_rebuild_if_wanted(conn))  # a plugin's old records went meanwhile
            raise _AlreadySealed
        # Asked before rewriting anything, so a pass cut short still rebuilds in the end.
        if _plaintext_left(conn, account, claimer_pass=claimer_pass):
            _set_meta(conn, _VACUUM_WANTED, "1")
        with _zeroing(conn):
            for table, cols in SEALED_COLUMNS:
                n = _seal_table(conn, table, cols, account, batch)
                if claimer_pass and table in _HAS_PARKED:
                    n += _seal_table(conn, table, cols, account, batch, parked=True)
                if n:
                    out[table] = n
            if claimer_pass:
                out["settings"] = move_legacy_settings(account)
            sealed = chats.seal_stored_messages(account, batch=batch, pause_s=pause_s)
            out.update({k: v for k, v in sealed.items() if v})
            out["search_records"] = sealed_records.seal_existing(account, batch=batch)
        if not _plaintext_left(conn, account, claimer_pass=claimer_pass):
            out.update(_rebuild_if_wanted(conn))
            _set_meta(conn, _DONE + account, str(time.time()))
            if claimer_pass and _get_meta(conn, _SEALED_AT) is None:
                _set_meta(conn, _CLAIMED_BY, account, replace=False)
                _set_meta(conn, _SEALED_AT, str(time.time()))
                out.update(_keep_a_plain_snapshot())
    except (data_crypto.Locked, _AlreadySealed):
        pass
    finally:
        with _lock:
            _sealing.discard(memo)
    out.update(_seal_backups(conn))
    if account == current():  # a pass that outlived a sign-in leaves the files to the new one's
        try:
            out.update(sealed_files.arrange_chat_files(account))
        except Exception:  # noqa: BLE001 — a folder in use: the next sign-in or start does it
            _log.warning("arranging chat attachments for the signed-in account failed", exc_info=True)
    return {k: v for k, v in out.items() if v}


def _rebuild_if_wanted(conn) -> dict[str, int]:
    """Plaintext was rewritten or deleted: the search indexes drop the old words (their
    segments are rows a rebuild would copy), then ducky.db is rebuilt at the next start."""
    if _get_meta(conn, _VACUUM_WANTED) is None:
        return {}
    with _zeroing(conn):
        for fts in ("message_fts", "search_fts"):
            with db.write_txn(conn):
                conn.execute(f"INSERT INTO {fts}({fts}) VALUES('optimize')")
    db.request_vacuum()
    with db.write_txn(conn):
        conn.execute("DELETE FROM meta WHERE key=?", (_VACUUM_WANTED,))
    return {"rebuild_next_start": 1}


def _keep_a_plain_snapshot() -> dict[str, int]:
    """A snapshot made now holds every claimed row sealed and no old fragments, so it stays
    plain: a corrupt ducky.db is always restored from it, never needing a sealed one."""
    from backend.store import sealed_files

    if not sealed_files.backup_files():
        return {}  # nothing from before to seal, nothing to keep beside it
    try:
        db.snapshot(label="sealed")
    except Exception:  # noqa: BLE001 — the daily snapshot makes one later
        _log.warning("snapshot after sealing failed", exc_info=True)
        return {}
    return {"plain_snapshot": 1}


def _seal_backups(conn) -> dict[str, int]:
    """ducky.db backups made before the claimed rows were all sealed, sealed for the account
    they went to (its public key: it needn't be the one signed in)."""
    from backend.store import sealed_files

    owner, sealed_at = claimer(), _get_meta(conn, _SEALED_AT)
    if not owner or sealed_at is None:
        return {}
    try:
        # Only copies started before then: one started after holds sealed rows only.
        n = sealed_files.seal_backups(owner, made_before=float(sealed_at))
    except Exception:  # noqa: BLE001
        _log.warning("sealing ducky.db backups from before failed", exc_info=True)
        return {}
    if sealed_files.backups_waiting(made_before=float(sealed_at)):
        # One written moments ago (the copy made before the upgrade) is sealed once it
        # has settled, not only at the next start.
        timer = threading.Timer(sealed_files.SETTLE_S + 1.0, _seal_backups_later, args=(str(db.db_path()),))
        timer.daemon = True
        timer.start()
    return {"backups": n} if n else {}


def _seal_backups_later(path: str) -> None:
    try:
        if str(db.db_path()) == path:
            _seal_backups(db.connect())
    except Exception:  # noqa: BLE001 — the next start tries again
        _log.warning("sealing ducky.db backups from before failed", exc_info=True)
