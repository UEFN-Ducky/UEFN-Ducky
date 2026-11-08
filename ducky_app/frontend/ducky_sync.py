"""Chat and Ducky sync between your UEFN Duckies, peer to peer through the sealed link
(:mod:`frontend.ducky_link`). The site relays ciphertext and keeps nothing.

- Duckies (your agent profiles) sync both ways: each Ducky pulls the others' and keeps
  whichever copy changed last (``repos.duckies.save_synced``). Sync only adds and
  updates: archive and delete stay each computer's own, and nothing is ever removed.
- Chats live on the Ducky they were started on (their agent runs there, with that
  computer's tools). Every other Ducky keeps a read-only copy of each one, its newest
  rows (``repos.ducky_mirrors``), to read while that Ducky is offline; to talk to it,
  open it on its home Ducky (``ducky_link_open``).
- A Ducky answers only while it's open with Remote access on; this one pulls from those
  every few minutes while signed in, and once more when asked (``sync_now``).
"""

from __future__ import annotations

import threading
from typing import Any

SYNC_EVERY_S = 180.0
ROWS_KEPT = 60
CHATS_LISTED = 200
CHATS_PER_ROUND = 8
_TEXT_KEPT = 4_000
_TOOL_TEXT_KEPT = 300
# A chat's rows as one answer: the sealed link takes ~48 KB of compressed ciphertext.
_ROWS_TEXT_BUDGET = 120_000

_thread: threading.Thread | None = None
_stop = threading.Event()
_wake = threading.Event()
_lock = threading.Lock()
_last: dict[str, Any] = {}


# --------------------------------------------------------------------------- answering


def duckies_for_sync() -> list[dict[str, Any]]:
    """This Ducky's own duckies for another Ducky of the account (``ducky_sync_duckies``):
    the signed-in account's edited shipped ones and custom ones, deleted ones left out."""
    from backend.store.repos import duckies as repo

    return [
        {"id": r["id"], "kind": r["kind"], "data": r["data"], "archived": r["archived"], "updated": r["updated"]}
        for r in repo.list_all(include_deleted=False)
        if r["kind"] in ("custom", "bundled") and r.get("data")
    ]


def chats_for_sync() -> list[dict[str, Any]]:
    """This Ducky's own chats, newest first, small (``ducky_sync_chats``)."""
    from frontend.ui_web.panel_api import shared_panel_api

    rows = [r for r in shared_panel_api().list_all_conversations(all_projects=True) if isinstance(r, dict) and r.get("id")]
    rows.sort(key=lambda r: float(r.get("updated") or 0), reverse=True)
    return [
        {
            "id": str(r["id"]),
            "title": str(r.get("title") or ""),
            "updated": float(r.get("updated") or 0),
            "project": str(r.get("project_name") or r.get("project_slug") or ""),
            "ducky": str(r.get("ducky_name") or ""),
        }
        for r in rows[:CHATS_LISTED]
    ]


def rows_for_sync(conv_id: str) -> list[dict[str, Any]]:
    """One of this Ducky's chats as another Ducky copies it (``ducky_sync_rows``): its
    newest rows, slim, oldest dropped first until they fit one sealed answer."""
    from frontend.ui_web.panel_api import shared_panel_api

    rows = slim_rows(shared_panel_api().load_messages(str(conv_id or ""), limit=ROWS_KEPT))
    while rows and sum(len(r["text"]) for r in rows) > _ROWS_TEXT_BUDGET:
        rows.pop(0)
    return rows


# --------------------------------------------------------------------------- pulling


def slim_rows(rows: Any) -> list[dict[str, Any]]:
    """Chat rows as a read-only copy keeps them: who and what, text capped."""
    out: list[dict[str, Any]] = []
    for r in rows if isinstance(rows, list) else []:
        if not isinstance(r, dict):
            continue
        role = str(r.get("role") or "")
        text = r.get("text") if isinstance(r.get("text"), str) else ""
        if role == "tool":
            out.append({"id": r.get("id"), "role": "tool", "text": text[:_TOOL_TEXT_KEPT]})
        elif text.strip():
            out.append({"id": r.get("id"), "role": role or "assistant", "text": text[:_TEXT_KEPT]})
    return out


def apply_duckies(rows: Any, account: str | None = None) -> int:
    """Take the other Ducky's duckies that changed there last, into ``account``'s (the
    signed-in one by default). Returns how many changed here."""
    from backend.store.repos import duckies as repo

    changed = 0
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        rid, kind, data = str(row.get("id") or ""), str(row.get("kind") or ""), row.get("data")
        if not rid or kind not in ("custom", "bundled") or not isinstance(data, dict) or not data:
            continue
        # One archived over there doesn't turn up here; one already here keeps its own state.
        if row.get("archived") and repo.get(rid, account=account) is None:
            continue
        if repo.save_synced(rid, kind, data, float(row.get("updated") or 0), account=account):
            changed += 1
    return changed


def _account() -> str:
    from backend.uefn_plugins.scopes import LOCAL, account_id

    account = account_id()
    return "" if account == LOCAL else account


def sync_with(peer: dict[str, Any]) -> dict[str, Any]:
    """One round with one other UEFN Ducky: its duckies, then its changed chats."""
    from backend.store.repos import ducky_mirrors as mirrors
    from frontend.ducky_link import call

    key, name = str(peer.get("keyId") or ""), str(peer.get("name") or "UEFN Ducky")
    account = _account()
    if not key or not account:
        return {"ok": False, "key": key, "error": "signed out"}
    out: dict[str, Any] = {"ok": True, "key": key, "ducky": name, "duckies": 0, "chats": 0}
    got = call(key, "ducky_sync_duckies", {})
    # An older Ducky without duckies sync still shares its chats.
    if got.get("ok"):
        out["duckies"] = apply_duckies(got.get("result"), account)
    listed = call(key, "ducky_sync_chats", {})
    if not listed.get("ok"):
        return {**out, "ok": False, "error": listed.get("error") or got.get("error")}
    need: list[tuple[float, str]] = []
    seen: set[str] = set()
    for row in listed.get("result") or []:
        if not isinstance(row, dict) or not row.get("id"):
            continue
        cid, updated = str(row["id"]), float(row.get("updated") or 0)
        seen.add(cid)
        if mirrors.note_chat(account, key, cid, str(row.get("title") or ""), updated):
            need.append((updated, cid))
    # Only when it listed everything it has: a short list would hide older copies.
    if len(seen) < CHATS_LISTED:
        mirrors.mark_gone(account, key, seen)
    for updated, cid in sorted(need, reverse=True)[:CHATS_PER_ROUND]:
        res = call(key, "ducky_sync_rows", {"conv_id": cid})
        if res.get("ok"):
            mirrors.put_rows(account, key, cid, slim_rows(res.get("result")), of=updated)
            out["chats"] += 1
    mirrors.note_peer(account, key, name)
    return out


def sync_round() -> list[dict[str, Any]]:
    """Every other Ducky of the account that's reachable now, one after another."""
    from frontend.ducky_link import list_duckies

    if not _account():
        return []
    listed = list_duckies()
    devices = [d for d in listed.get("devices") or [] if isinstance(d, dict)]
    prune_removed(devices)
    results = [sync_with(d) for d in devices if d.get("live") and not d.get("mine")]
    with _lock:
        _last.clear()
        _last.update({"results": results})
    return results


def prune_removed(devices: list[dict[str, Any]]) -> int:
    """Drop the copies of Duckies no longer on the account (removed on the site or
    another PC). Only trusts a full list: one that names this Ducky too."""
    from backend.store.repos import ducky_mirrors as mirrors

    account = _account()
    keys = {str(d.get("keyId") or "") for d in devices}
    if not account or not any(d.get("mine") for d in devices):
        return 0
    gone = [p["ducky"] for p in mirrors.peers(account) if p["ducky"] not in keys]
    for key in gone:
        mirrors.forget_peer(account, key)
    return len(gone)


def last_errors() -> dict[str, str]:
    """Why the last round couldn't sync a Ducky, by its key."""
    with _lock:
        return {str(r.get("key")): str(r.get("error") or "didn't answer") for r in _last.get("results") or [] if not r.get("ok") and r.get("key")}


def _loop() -> None:
    while not _stop.is_set():
        try:
            sync_round()
        except Exception:
            pass  # offline or signed out: the next round tries again
        _wake.wait(SYNC_EVERY_S)
        _wake.clear()


def start() -> None:
    """While signed in: pull from the other Duckies every few minutes."""
    global _thread
    with _lock:
        if _thread is not None and _thread.is_alive():
            if not _stop.is_set():
                return
            # Stopped a moment ago (sign-out, then in again): let that round end first.
            _thread.join(timeout=5)
        _stop.clear()
        _thread = threading.Thread(target=_loop, daemon=True, name="ducky-sync")
        _thread.start()


def stop() -> None:
    _stop.set()
    _wake.set()


def sync_now() -> None:
    _wake.set()


# --------------------------------------------------------------------------- reading


def mirror_list() -> list[dict[str, Any]]:
    """The other Duckies' chats copied here (no rows), for the sidebar."""
    from backend.store.repos import ducky_mirrors as mirrors

    account = _account()
    return mirrors.peers(account) if account else []


def forget(ducky: str) -> int:
    """Drop every copy of one other Ducky's chats. Returns how many chats went."""
    from backend.store.repos import ducky_mirrors as mirrors

    account = _account()
    return mirrors.forget_peer(account, str(ducky or "")) if account else 0


def mirror_rows(ducky: str, conv_id: str) -> dict[str, Any] | None:
    from backend.store.repos import ducky_mirrors as mirrors

    account = _account()
    return mirrors.rows(account, str(ducky or ""), str(conv_id or "")) if account else None
