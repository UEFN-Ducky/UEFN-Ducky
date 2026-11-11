"""Chats made on this PC while signed out, as the signed-in account sees them.

Chats belong to an account (:mod:`backend.store.account_rows`), so signing in listed only
the account's: every chat made before signing in was gone from the list until the person
signed out again (Nov 2026). Nothing was lost, and nothing said so. Here the signed-in
account can see them (:func:`waiting`), take them (:func:`adopt`) or delete them
(:func:`remove`).

Taking a chat moves its rows: opened with this PC's key, sealed again for the account. The
chat keeps its id, so everything stored under that id stays with it (attachments, plans,
runs). What belongs together moves together: a chat with its sub-agent chats, a group with
every chat and folder in it. A chat keeps its folders too: the account gets the folder if it
has none by that id, and a folder left empty behind is dropped.

Signed out these chats are simply the list; every function here then does nothing.
"""

from __future__ import annotations

import functools
import logging
import time
from typing import Any

from backend.store import account_rows, db
from backend.store.repos import chats

LOCAL = "_local"  # backend.uefn_plugins.scopes.LOCAL
ARCHIVE = "archive"  # frontend.archive_folder.ARCHIVE_FOLDER_ID

_log = logging.getLogger("uefn_ducky.store.local_chats")

_Folder = tuple[str, str]  # (project id, folder id): folder ids repeat across projects


def signed_in() -> str | None:
    """The signed-in account, or None signed out."""
    account = account_rows.current()
    return None if account in (LOCAL, account_rows.UNCLAIMED) else account


# --------------------------------------------------------------------------- what moves together


def _local_folders(conn) -> dict[_Folder, tuple[str, str]]:
    """(project, id) -> (parent id, group hub id) of the folders made signed out."""
    return {
        (str(r[0]), str(r[1])): (str(r[2] or ""), str(r[3] or ""))
        for r in conn.execute(
            "SELECT project_id, id, parent_id, group_hub_id FROM folders WHERE account_id=?", (LOCAL,)
        )
    }


def _chain(folders: dict[_Folder, tuple[str, str]], project: str, folder_id: str) -> list[_Folder]:
    """The folder and the folders it sits in, innermost first (Archive is every account's own)."""
    out: list[_Folder] = []
    seen: set[str] = set()
    while folder_id and folder_id != ARCHIVE and folder_id not in seen and (project, folder_id) in folders:
        seen.add(folder_id)
        out.append((project, folder_id))
        folder_id = folders[(project, folder_id)][0]
    return out


def _units(conn) -> list[dict[str, Any]]:
    """The chats made signed out, in the sets that move together. Each: ``head`` (the chat
    that stands for it), ``chats``, ``folders`` (a group's own), ``group``, ``archived``,
    ``updated``, ``title`` (stored, still sealed)."""
    rows = {
        str(r[0]): r
        for r in conn.execute(
            "SELECT id, project_id, folder_id, parent_conv_id, title, updated FROM conversations WHERE account_id=?",
            (LOCAL,),
        )
    }
    if not rows:
        return []
    folders = _local_folders(conn)

    def group_of(project: str, folder_id: str) -> str:
        """The outermost group folder the folder sits in ('' when it is in no group)."""
        top = ""
        for key in _chain(folders, project, folder_id):
            if folders[key][1]:
                top = key[1]
        return top

    def unit_key(conv_id: str) -> tuple[str, str, str]:
        seen: set[str] = set()
        while True:
            row = rows[conv_id]
            project = str(row[1])
            group = group_of(project, str(row[2] or ""))
            if group:
                return ("group", project, group)
            parent = str(row[3] or "")
            seen.add(conv_id)
            if not parent or parent not in rows or parent in seen:
                return ("chat", conv_id, "")
            conv_id = parent

    by_key: dict[tuple[str, str, str], list[str]] = {}
    for conv_id in rows:
        by_key.setdefault(unit_key(conv_id), []).append(conv_id)
    units: list[dict[str, Any]] = []
    for (kind, a, b), ids in by_key.items():
        own: list[_Folder] = []
        if kind == "group":
            hub = folders[(a, b)][1]
            head = hub if hub in ids else max(ids, key=lambda i: float(rows[i][5] or 0.0))
            own = [key for key in folders if key[0] == a and (a, b) in _chain(folders, a, key[1])]
        else:
            head = a
        units.append({
            "head": head,
            "chats": ids,
            "folders": own,
            "group": kind == "group",
            "archived": str(rows[head][2] or "") == ARCHIVE,
            "updated": max(float(rows[i][5] or 0.0) for i in ids),
            "title": str(rows[head][4] or ""),
        })
    return units


def _chosen(conn, conv_ids: list[str] | None) -> list[dict[str, Any]]:
    units = _units(conn)
    if conv_ids is None:
        return units
    wanted = {str(i) for i in conv_ids}
    return [u for u in units if wanted & set(u["chats"])]


def waiting() -> list[dict[str, Any]]:
    """What the signed-in account can take, newest first: ``{id, title, updated, chats,
    group}``, one row per chat or per group (with everything in it). Archived ones are left
    out. Signed out, or nothing made signed out: empty."""
    if signed_in() is None:
        return []
    out: list[dict[str, Any]] = []
    for unit in _units(db.connect()):
        if unit["archived"]:
            continue
        try:
            title = account_rows.open_cached(unit["title"], LOCAL)
        except Exception:  # noqa: BLE001 — a title that won't open still lists (and can be taken or deleted)
            title = ""
        out.append({
            "id": unit["head"],
            "title": title,
            "updated": unit["updated"],
            "chats": len(unit["chats"]),
            "group": unit["group"],
        })
    out.sort(key=lambda row: (-row["updated"], row["id"]))
    return out


# --------------------------------------------------------------------------- taking them


def adopt(conv_ids: list[str] | None = None) -> dict[str, Any]:
    """Move chats made signed out into the signed-in account: the ones named (each with
    what belongs to it), or every one with ``None`` (archived ones too, into the account's
    Archive). One set at a time, each whole or not at all.

    Returns ``{"moved": [chat ids], "failed": [the ids that stood for a set that stayed]}``.
    Raises ``data_crypto.Locked`` while the account's data key isn't on this PC."""
    account = signed_in()
    if account is None:
        return {"moved": [], "failed": []}
    key = account_rows.ready(account)
    conn = db.connect()
    moved: list[str] = []
    failed: list[str] = []
    for unit in _chosen(conn, conv_ids):
        try:
            _adopt_unit(conn, unit, account, key)
        except Exception:  # noqa: BLE001 — that set stays as it was, whole; the others still move
            _log.exception("a chat made signed out could not be moved into the account")
            failed.append(unit["head"])
            continue
        moved += unit["chats"]
    return {"moved": moved, "failed": failed}


def _adopt_unit(conn, unit: dict[str, Any], account: str, key: bytes) -> None:
    digest_of = functools.partial(account_rows.part_digest, key=key)
    # Opening and sealing happen before the write lock; the transaction only writes.
    prepared = [_prepare(conn, conv_id, account, key, digest_of) for conv_id in unit["chats"]]
    folders = _local_folders(conn)
    needed: list[_Folder] = list(unit["folders"])
    for prep in prepared:
        needed += _chain(folders, prep["project"], prep["folder"])
    needed = list(dict.fromkeys(needed))
    copies = []
    for project, folder_id in needed:
        row = conn.execute(
            "SELECT name, parent_id, sort_order, group_hub_id FROM folders WHERE account_id=? AND project_id=? AND id=?",
            (LOCAL, project, folder_id),
        ).fetchone()
        if row is not None:
            name = account_rows.seal(account_rows.open_text(str(row[0] or ""), LOCAL), account)
            copies.append((account, project, folder_id, name, str(row[1] or ""), float(row[2] or 0.0), str(row[3] or "")))
    with db.write_txn(conn):
        for prep in prepared:
            _write(conn, prep, account)
        conn.executemany(
            "INSERT OR IGNORE INTO folders(account_id, project_id, id, name, parent_id, sort_order, group_hub_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            copies,
        )
        _drop_emptied(conn, needed)
    for prep in prepared:
        chats._opened_forget(prep["id"])
        _bring_ducky(prep["profile"], account)


def _prepare(conn, conv_id: str, account: str, key: bytes, digest_of) -> dict[str, Any]:
    """Everything one chat's move writes, read and sealed for ``account``."""
    row = conn.execute(
        "SELECT title, state, msg_rev, project_id, folder_id, profile_id FROM conversations WHERE id=? AND account_id=?",
        (conv_id, LOCAL),
    ).fetchone()
    if row is None:
        raise LookupError("the chat is no longer one made signed out")
    reader = chats._conv_parts(conn, conv_id, LOCAL)
    messages = []
    unread: list[tuple[str, int]] = []
    for row_id, seq, stored_hash, body, fmt in conn.execute(
        "SELECT id, seq, hash, body, fmt FROM messages WHERE conv_id=? ORDER BY seq", (conv_id,)
    ).fetchall():
        try:
            message = chats._message(str(body), int(fmt), LOCAL, lambda: reader)
        except ValueError:
            # No read could make a message of this row before the move either (reads skip
            # it). It goes along as it is, sealed for its new owner: left sealed for this
            # PC, opening it with the account's key would fail the whole chat's load.
            unread.append((account_rows.seal(account_rows.open_text(str(body), LOCAL), account), int(row_id)))
            continue
        prep = chats._prep_row(message, chats._dumps(message), str(stored_hash), account, key, digest_of)
        messages.append((int(seq), (str(stored_hash), int(row_id)), prep))
    return {
        "id": conv_id,
        "rev": int(row[2] or 0),
        "project": str(row[3] or ""),
        "folder": str(row[4] or ""),
        "profile": str(row[5] or ""),
        "title": account_rows.seal(account_rows.open_text(str(row[0] or ""), LOCAL), account),
        "state": account_rows.seal(account_rows.open_text(str(row[1] or "{}"), LOCAL), account),
        "messages": messages,
        "unread": unread,
    }


def _write(conn, prep: dict[str, Any], account: str) -> None:
    conv_id = prep["id"]
    now = conn.execute("SELECT account_id, msg_rev FROM conversations WHERE id=?", (conv_id,)).fetchone()
    if now is None or str(now[0]) != LOCAL or int(now[1] or 0) != prep["rev"]:
        raise RuntimeError("the chat changed while it was being moved")
    for seq, row, message in prep["messages"]:
        chats._write_row(conn, conv_id, seq, row, message, account, refresh=False)
    conn.executemany("UPDATE messages SET body=? WHERE id=?", prep["unread"])
    conn.execute("UPDATE messages SET account_id=? WHERE conv_id=?", (account, conv_id))
    conn.execute(
        "UPDATE conversations SET account_id=?, title=?, state=? WHERE id=? AND account_id=?",
        (account, prep["title"], prep["state"], conv_id, LOCAL),
    )


def _drop_emptied(conn, folders: list[_Folder]) -> None:
    """Folders the move left with nothing made signed out in them go (the account has its
    own row of each now); one that still holds a chat or a folder stays for both."""
    left = list(folders)
    while True:
        gone = [
            (project, folder_id)
            for project, folder_id in left
            if conn.execute(
                "SELECT 1 FROM conversations WHERE account_id=? AND project_id=? AND folder_id=? LIMIT 1",
                (LOCAL, project, folder_id),
            ).fetchone() is None
            and conn.execute(
                "SELECT 1 FROM folders WHERE account_id=? AND project_id=? AND parent_id=? LIMIT 1",
                (LOCAL, project, folder_id),
            ).fetchone() is None
        ]
        if not gone:
            return
        conn.executemany(
            "DELETE FROM folders WHERE account_id=? AND project_id=? AND id=?",
            [(LOCAL, project, folder_id) for project, folder_id in gone],
        )
        left = [key for key in left if key not in gone]


def _bring_ducky(profile_id: str, account: str) -> None:
    """A chat's own ducky (one the person made signed out) comes with it as a copy, unless
    the account already has a ducky by that id. Shipped duckies are everyone's already."""
    if not profile_id:
        return
    try:
        from backend.store.repos import duckies

        theirs = duckies.get(profile_id, account=LOCAL)
        if theirs is None or theirs["kind"] != "custom" or theirs["deleted"] or not theirs["data"]:
            return
        if duckies.get(profile_id, account=account) is None:
            duckies.save(profile_id, "custom", theirs["data"], account=account)
    except Exception:  # noqa: BLE001 — the chat moved; it opens with the default ducky
        _log.warning("a moved chat's ducky could not be copied to the account", exc_info=True)


# --------------------------------------------------------------------------- deleting them


def remove(conv_ids: list[str]) -> list[tuple[str, str]]:
    """Delete the named chats made signed out, each with what belongs to it, for good.
    Returns ``(chat id, project id)`` of every chat deleted, for the caller to remove
    their folders on disk. Signed out: nothing (the list's own delete does it)."""
    if signed_in() is None or not conv_ids:
        return []
    conn = db.connect()
    deleted: list[tuple[str, str]] = []
    for unit in _chosen(conn, conv_ids):
        with db.write_txn(conn):
            rows = [
                (str(r[0]), str(r[1]))
                for r in conn.execute(
                    f"SELECT id, project_id FROM conversations WHERE account_id=? AND id IN ({','.join('?' * len(unit['chats']))})",
                    (LOCAL, *unit["chats"]),
                )
            ]
            for conv_id, _project in rows:
                conn.execute("DELETE FROM messages WHERE conv_id=?", (conv_id,))
                conn.execute("DELETE FROM conversations WHERE id=? AND account_id=?", (conv_id, LOCAL))
            chats._mark_deleted(conn, chats._DELETED_KEY, [conv_id for conv_id, _project in rows], time.time())
            _drop_emptied(conn, unit["folders"])
        for conv_id, _project in rows:
            chats._opened_forget(conv_id)
        deleted += rows
    return deleted
