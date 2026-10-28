"""Something new arrived: tells the person (and agents) when another PC pushed to the island.

Two PCs share one island through Unreal Revision Control (``revision_push`` /
``revision_pull``). A thread in the app process watches the island UEFN has open:

- the listener's health GET names it (``island_dir``: served off the editor thread, nothing
  queues in UEFN); no listener or no island, nothing runs but that GET;
- one ``lore --remote history 1 --branch main`` per round reads the remote head (~1 s); while
  the island is behind, ``lore status`` too, so a pull made anywhere clears the alert;
- only when one of them moved, ``lore_cli.repo_state`` (3 calls) says which remote revisions
  the island lacks and who made them;
- only for news worth an alert, ``lore_cli.overlap`` (1 diff, +1 when this PC has autosaves
  main lacks) names the files changed on both PCs: the alert says "pull with care";
- while the island stays behind after that alert, the overlap again each round with the
  working tree ``lore status`` just listed (a file edited outside UEFN, in VS Code, shows
  there a little later): a file newly changed on both PCs gives one follow-up alert.

Revisions made on another PC (a check-in of this PC's own autosave doesn't count) give one
alert per new remote head: a Windows notification when no Ducky window is in front, the
account's bell, and an ``island_updates`` panel event (a row in the header's activity list).
It never pulls: a pull swaps the map, so only the person, or a Ducky they ask
(``revision_pull``, which refuses over unsaved work), does it.

What it found is kept in ``workspace_state`` (:data:`KEY`) for every process:
``ducky_get_status`` shows it to agents (:func:`for_agents`).

Each round that reached lore also reads the island's file locks (``lore lock query``, ~1 s):
UEFN locks a file on the server as soon as an actor is modified, until check-in or revert, so
a lock held by the other PC's account means "they're editing it, don't touch". The latest list
goes to ``workspace_state`` (:data:`LOCKS_KEY`); :func:`others_locks` reads it from any process
(the MCP bridge's guard), :func:`locks_for_agents` is what ``ducky_get_status`` shows. Locks
never alert.

Rounds: every :data:`ACTIVE_S` while the person uses this PC, :data:`IDLE_S` once they've
been away :data:`notifications.AWAY_S`; a failing lore call backs off to :data:`ERROR_MAX_S`.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

ACTIVE_S = 10.0  # the person is at this PC: news shows in ~5 s on average, 10-15 s at worst
IDLE_S = 60.0  # away from the keyboard: the toast waits for them anyway
NO_ISLAND_S = 15.0  # no listener / no island open: only the local health GET
NOT_LORE_S = 120.0  # the open island has no .lore folder
ERROR_MAX_S = 300.0
KEY = "island_updates"  # workspace_state doc
LOCKS_KEY = "island_locks"  # workspace_state doc: the open island's file locks
LOCKS_STALE_ROUNDS = 3  # a locks doc older than this many rounds (+ slack) means nothing
LOCKS_SLACK_S = 15.0  # lore calls take ~1 s each; leaves room for a slow round

_lock = threading.Lock()
_wake = threading.Event()
_stop = threading.Event()
_thread: threading.Thread | None = None
_st: dict[str, Any] = {}


def _reset(island: str = "") -> None:
    _st.clear()
    # state: the last behind repo_state (the overlap's input); both: files changed on both PCs.
    _st.update(island=island, remote_sig="", local_sig="", behind=False, told="", fails=0, both=[], state=None)
    if island:
        doc = _read_doc()
        if doc and doc.get("island") == island:
            _st["told"] = str(doc.get("told") or "")  # a restart doesn't tell the same news twice
            _st["both"] = list(doc.get("both_changed") or [])


_reset()


# --------------------------------------------------------------------------- seams (tests patch)


def _lore():
    from frontend.ui_web.verse_editor.urc import lore_cli

    return lore_cli


def _open_island() -> str:
    from backend.bridge import listener_get_health
    from frontend.settings import PANEL_LISTENER_PORT

    health = listener_get_health(PANEL_LISTENER_PORT)
    if not health or health.get("status") != "ok":
        return ""
    return os.path.normpath(str(health.get("island_dir") or "")) if health.get("island_dir") else ""


def _person_away() -> bool:
    from frontend import notifications

    idle = notifications._idle_seconds()
    return idle is not None and idle >= notifications.AWAY_S


def _alert(title: str, status: str, detail: str) -> None:
    from frontend.notifications import alert

    alert(title, status, level="info", detail=detail)


def _push(event: dict[str, Any]) -> None:
    try:
        from frontend.ui_web.agent_modes import push_ui_event

        push_ui_event(event)
    except Exception:  # noqa: BLE001 — the alert and the status still say it
        _log.debug("island_updates push failed", exc_info=True)


def _kv_get(key: str) -> dict[str, Any] | None:
    try:
        from backend.store.repos import kv

        doc = kv.get_doc("workspace_state", key)
        return doc if isinstance(doc, dict) else None
    except Exception:  # noqa: BLE001
        return None


def _kv_set(key: str, doc: dict[str, Any] | None) -> None:
    try:
        from backend.store.repos import kv

        if doc is None:
            kv.delete_doc("workspace_state", key)
        else:
            kv.set_doc("workspace_state", key, doc)
    except Exception:  # noqa: BLE001 — agents just don't see it this round
        _log.debug("%s doc write failed", key, exc_info=True)


def _read_doc() -> dict[str, Any] | None:
    return _kv_get(KEY)


def _write_doc(doc: dict[str, Any] | None) -> None:
    _kv_set(KEY, doc)


def _read_locks() -> dict[str, Any] | None:
    return _kv_get(LOCKS_KEY)


def _write_locks(doc: dict[str, Any] | None) -> None:
    _kv_set(LOCKS_KEY, doc)


def norm_path(p: str) -> str:
    """One spelling for a file path on both sides (this watch, the bridge guard):
    absolute, ``os.path.normcase`` (lower case on Windows), forward slashes."""
    return os.path.normcase(os.path.abspath(p)).replace("\\", "/")


# --------------------------------------------------------------------------- one round


def news_text(name: str, news: list[dict[str, Any]]) -> str:
    """ "3 new changes on test_ducky_v2 from Mich ou: “moved the cube”" """
    n = len(news)
    authors = list(dict.fromkeys(r["creator"] for r in news if r.get("creator")))
    text = f"{n} new change{'s' if n != 1 else ''} on {name}"
    if authors:
        text += " from " + ", ".join(authors)
    if news and news[0].get("message"):
        text += f": “{news[0]['message']}”"
    return text


def both_text(rels: list[str]) -> str:
    """ "1 file changed on both PCs: Content/x.verse — pull with care" ("" for none)."""
    if not rels:
        return ""
    n = len(rels)
    shown = ", ".join(rels[:3]) + (f" (+{n - 3} more)" if n > 3 else "")
    return f"{n} file{'s' if n != 1 else ''} changed on both PCs: {shown} — pull with care"


def _both_changed(island: str, state: dict[str, Any], lore: Any) -> list[str]:
    """Files the remote changed that this PC changed too (rel paths); [] when lore can't say."""
    try:
        res = lore.overlap(island, state=state)
    except Exception as exc:  # noqa: BLE001
        res = {"ok": False, "error": str(exc)}
    if not res.get("ok"):
        _log.debug("island watch overlap: %s", res.get("error"))
        return []
    return [str(f["rel"]) for f in res.get("files") or []]


def _interval() -> float:
    try:
        return IDLE_S if _person_away() else ACTIVE_S
    except Exception:  # noqa: BLE001
        return ACTIVE_S


def _failed(error: str) -> float:
    _st["fails"] += 1
    _log.debug("island watch: %s", error)
    return min(ERROR_MAX_S, _interval() * (2 ** _st["fails"]))


def _apply(island: str, state: dict[str, Any], lore: Any) -> None:
    name = Path(island).name
    was_behind = _st["behind"]
    incoming = list(state.get("incoming") or [])
    news = [r for r in incoming if not r.get("own")]
    behind = bool(state.get("behind"))
    _st.update(remote_sig=str(state.get("remote_signature") or ""),
               local_sig=str(state.get("local_signature") or ""), behind=behind)
    text = news_text(name, news) if news else ""
    _st["state"] = state if behind else None
    if not behind:
        _st["both"] = []
    if behind and news and _st["remote_sig"] != _st["told"]:
        _st["told"] = _st["remote_sig"]
        _st["both"] = _both_changed(island, state, lore)
        detail = text + (f"; {both_text(_st['both'])}" if _st["both"] else "")
        _alert(name, f"{len(news)} new change{'s' if len(news) != 1 else ''} to pull.",
               f"{detail}. Pull when you're ready: ask a Ducky to run revision_pull "
               "(it never pulls over unsaved work).")
        _push({"type": "island_updates", "island": island, "name": name, "behind": True,
               "count": len(news), "detail": detail})
    elif was_behind and not behind:
        _push({"type": "island_updates", "island": island, "name": name, "behind": False, "count": 0,
               "detail": f"{name} is up to date."})
    _write_doc({
        "island": island,
        "name": name,
        "behind": behind,
        "new_changes": len(news) if behind else 0,
        "authors": list(dict.fromkeys(r["creator"] for r in news if r.get("creator"))) if behind else [],
        "latest": ({"revision": news[0]["revision"], "by": news[0]["creator"], "message": news[0]["message"]}
                   if behind and news else None),
        "text": text if behind else "",
        "both_changed": list(_st["both"]),
        "local_revision": state.get("local_revision"),
        "remote_revision": state.get("remote_revision"),
        "told": _st["told"],
        "checked_at": int(time.time()),
        "pid": os.getpid(),
    })


def _recheck_both(island: str, local: dict[str, Any], lore: Any) -> None:
    """Behind and already told: the overlap again with this round's working tree (1 diff).
    Files newly changed on both PCs give one follow-up alert; the doc's list stays current."""
    if not _st["state"] or not _st["told"]:
        return
    state = {**_st["state"], "changes": local.get("changes", _st["state"].get("changes") or [])}
    both = _both_changed(island, state, lore)
    if both == _st["both"]:
        return
    new = [rel for rel in both if rel not in _st["both"]]
    _st["both"] = both
    name = Path(island).name
    if new:
        shown = ", ".join(new[:3]) + (f" (+{len(new) - 3} more)" if len(new) > 3 else "")
        detail = f"{shown} {'was' if len(new) == 1 else 'were'} also changed on this PC — pull with care"
        _alert(name, "Changed on both PCs.", f"{detail}. Ask a Ducky to run revision_pull when you're ready.")
        _push({"type": "island_updates", "island": island, "name": name, "behind": True,
               "count": len(new), "detail": detail})
    doc = _read_doc()
    if doc and doc.get("island") == island:
        _write_doc({**doc, "both_changed": list(both), "checked_at": int(time.time())})


def _refresh_locks(island: str, lore: Any, every_s: float) -> None:
    """One ``lore lock query``; the doc is replaced only when it worked (else it goes stale)."""
    try:
        res = lore.locks(island)
    except Exception as exc:  # noqa: BLE001
        res = {"ok": False, "error": str(exc)}
    if not res.get("ok"):
        _log.debug("island watch locks: %s", res.get("error"))
        return
    rows = [{k: lk.get(k) for k in ("path", "rel", "owner", "mine")} for lk in res.get("locks") or []]
    _write_locks({
        "island": island,
        "me": str(res.get("me") or ""),
        "locks": rows,
        "others": [lk["rel"] for lk in rows if not lk["mine"]],
        "checked_at": int(time.time()),
        "every_s": every_s,
        "pid": os.getpid(),
    })


def tick() -> float:
    """One round. Returns the seconds until the next one. Never raises."""
    try:
        island = _open_island()
    except Exception:  # noqa: BLE001
        island = ""
    with _lock:
        if island != _st["island"]:
            _reset(island)
            _write_locks(None)
            if not island:
                _write_doc(None)
        if not island:
            return NO_ISLAND_S
        if not (Path(island) / ".lore").is_dir():
            return NOT_LORE_S
        lore = _lore()
        delay = _updates_round(island, lore)
        if _st["fails"] == 0:  # lore answered this round: the locks too (one more call)
            _refresh_locks(island, lore, delay)
        return delay


def _updates_round(island: str, lore: Any) -> float:
    """The remote head (and what's new) part of a round; returns the next delay."""
    head = lore.remote_head(island)
    if not head.get("ok"):
        return _failed(str(head.get("error") or "remote head"))
    local_moved = False
    if _st["behind"]:
        local = lore.local_head(island)
        if not local.get("ok"):
            return _failed(str(local.get("error") or "local head"))
        local_moved = local["signature"] != _st["local_sig"]
        if head["signature"] == _st["remote_sig"] and not local_moved:
            _recheck_both(island, local, lore)
    if head["signature"] == _st["remote_sig"] and not local_moved:
        _st["fails"] = 0
        return _interval()
    state = lore.repo_state(island)
    if not state.get("ok"):
        return _failed(str(state.get("error") or "repo state"))
    _st["fails"] = 0
    _apply(island, state, lore)
    return _interval()


# --------------------------------------------------------------------------- for agents


def for_agents() -> dict[str, Any] | None:
    """What ``ducky_get_status`` shows (any process): the open island's news, or None when
    nothing watches it (the app is closed, no Lore island open)."""
    doc = _read_doc()
    if not doc or not doc.get("island"):
        return None
    pid = int(doc.get("pid") or 0)
    if pid and pid != os.getpid():
        try:
            import psutil

            if not psutil.pid_exists(pid):
                return None
        except Exception:  # noqa: BLE001
            pass
    out = {k: doc.get(k) for k in ("island", "name", "behind", "new_changes", "authors", "latest",
                                   "both_changed", "local_revision", "remote_revision", "checked_at")}
    if doc.get("behind"):
        out["hint"] = ("Another PC pushed changes to this island. Tell the person; pull only when they "
                       "agree (revision_pull never pulls over unsaved work).")
        if doc.get("both_changed"):
            out["hint"] += (" Files in both_changed were changed on both PCs: ask the person which version "
                            "to keep (this PC's, the other PC's, or merge by hand) before pulling.")
    return out


def _fresh_locks(island: str = "") -> dict[str, Any] | None:
    """The locks doc when it is recent (and for ``island`` when given), else None."""
    doc = _read_locks()
    if not doc or not doc.get("island"):
        return None
    if island and norm_path(str(doc["island"])) != norm_path(island):
        return None
    every = float(doc.get("every_s") or ACTIVE_S)
    age = time.time() - float(doc.get("checked_at") or 0)
    if not -LOCKS_SLACK_S <= age <= LOCKS_STALE_ROUNDS * every + LOCKS_SLACK_S:
        return None
    return doc


def others_locks(island: str) -> dict[str, str]:
    """``{norm_path(file): owner display name}``: files of ``island`` locked by another PC's
    account, from the latest round (any process). ``{}`` when the doc is stale (the watch
    stopped, lore failed for a few rounds), for another island, or nothing is locked.
    Never raises."""
    try:
        doc = _fresh_locks(island) if island else None
        if doc is None:
            return {}
        return {norm_path(str(lk["path"])): str(lk.get("owner") or "")
                for lk in doc.get("locks") or [] if lk.get("path") and not lk.get("mine")}
    except Exception:  # noqa: BLE001
        return {}


def locks_for_agents(limit: int = 20) -> dict[str, Any] | None:
    """What ``ducky_get_status`` shows: the open island's files locked by another PC (compact),
    or None when there are none or the list is stale."""
    try:
        doc = _fresh_locks()
    except Exception:  # noqa: BLE001
        return None
    others = [lk for lk in (doc or {}).get("locks") or [] if not lk.get("mine")]
    if not others:
        return None
    return {
        "island": doc["island"],
        "count": len(others),
        "locked_by_others": [{"file": lk.get("rel"), "by": lk.get("owner")} for lk in others[:limit]],
        "hint": ("Another PC is editing these files (Unreal Revision Control locks): don't modify "
                 "them until it checks in; tell the person."),
    }


# --------------------------------------------------------------------------- the thread


def wake() -> None:
    """Check again now (after a pull or push made from this process)."""
    _wake.set()


def _loop() -> None:
    while not _stop.is_set():
        delay = tick()
        _wake.wait(delay)
        _wake.clear()


def start() -> None:
    global _thread
    with _lock:
        if _thread is not None and _thread.is_alive():
            return
        _stop.clear()
        _thread = threading.Thread(target=_loop, daemon=True, name="island-watch")
        _thread.start()


def stop() -> None:
    _stop.set()
    _wake.set()


def reset_for_tests() -> None:
    with _lock:
        _reset()
    _wake.clear()
