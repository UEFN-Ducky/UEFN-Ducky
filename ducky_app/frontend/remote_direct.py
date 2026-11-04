"""Direct (tunnel-free) Remote View signaling on the desktop.

The phone sends one full-ICE offer through the site mailbox (`rtc_connect`)
or, for local end-to-end runs, straight to the loopback panel API
(`direct_rtc_connect`). Either way the offer is handed to the WebView2 page
as a panel event; the page builds the peer connection and posts the
full-ICE answer back through `direct_rtc_answer`. This module is the
rendezvous between that RPC thread and the page.

ponytail: one round trip, no trickle, no queue — the site plugin runs as a
short subprocess per request and cannot hold a connection, so trickle ICE
would cost a mailbox write per candidate.
"""

from __future__ import annotations

import threading
import time
from typing import Any

from frontend import __version__

PROTOCOL_VERSION = 1
ANSWER_WAIT_S = 20.0
MAX_SDP_BYTES = 64 * 1024

_lock = threading.Lock()
_waiters: dict[str, threading.Event] = {}
_answers: dict[str, dict[str, Any]] = {}
_last_report: dict[str, Any] = {}
# A viewer whose mailbox reply got lost asks again with the same session: it gets the
# answer the page already made (or joins the wait still running), never a second offer.
RESUME_KEEP_S = 60.0
_done: dict[str, tuple[float, dict[str, Any]]] = {}
# Sessions that timed out: a late answer from the page is kept for the viewer's retry.
_late: dict[str, float] = {}
_LINK_KEYS = ("state", "reason", "connect_ms", "candidate", "attempt")
# Phone link health, bucketed by usage_stats.remote_link; client/net/viewer_version ride along.
_HEALTH_KEYS = ("quality", "input", "load", "end", "client", "net", "viewer_version")


def _clean_sdp(desc: Any) -> dict[str, str] | None:
    if not isinstance(desc, dict):
        return None
    kind = str(desc.get("type") or "").strip().lower()
    sdp = desc.get("sdp")
    if kind not in ("offer", "answer") or not isinstance(sdp, str) or not sdp.strip():
        return None
    if len(sdp.encode("utf-8")) > MAX_SDP_BYTES:
        return None
    return {"type": kind, "sdp": sdp}


def _clean_ice(ice: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not isinstance(ice, list):
        return out
    for row in ice[:8]:
        if not isinstance(row, dict) or not row.get("urls"):
            continue
        item: dict[str, Any] = {"urls": row["urls"]}
        if isinstance(row.get("username"), str):
            item["username"] = row["username"]
        if isinstance(row.get("credential"), str):
            item["credential"] = row["credential"]
        out.append(item)
    return out


def rtc_connect(args: dict[str, Any] | None) -> dict[str, Any]:
    out = _connect(args)
    try:
        from frontend import usage_stats

        usage_stats.remote_connect(out)
    except Exception:
        pass
    return out


def _connect(args: dict[str, Any] | None) -> dict[str, Any]:
    """Hand a viewer offer to the page and wait for its answer.

    Returns ``{"answer": {...}, "version": ..., "protocol": ...}`` or
    ``{"error": ...}``. Never raises: the mailbox result must always carry a
    readable reason so the phone can explain instead of spinning.
    """
    raw = args if isinstance(args, dict) else {}
    session = str(raw.get("session") or "").strip()
    offer = _clean_sdp(raw.get("offer"))
    protocol = int(raw.get("protocol") or 0)
    if not session or len(session) > 64 or not session.isalnum():
        return {"error": "invalid session"}
    if offer is None or offer["type"] != "offer":
        return {"error": "invalid offer"}
    if protocol != PROTOCOL_VERSION:
        return {"error": "protocol mismatch", "protocol": PROTOCOL_VERSION, "version": __version__}
    with _lock:
        _prune(time.monotonic())
        kept = _done.get(session)
        if kept is not None and not kept[1].get("error"):
            return _reply(kept[1])
        ev = _waiters.get(session)
        owner = ev is None
        if owner:
            ev = _waiters[session] = threading.Event()
            _answers.pop(session, None)
            _done.pop(session, None)
            _late.pop(session, None)
    if owner:
        from frontend.ui_web.panel_httpd import publish_panel_events

        publish_panel_events(
            [
                {
                    "type": "direct_rtc",
                    "session": session,
                    "offer": offer,
                    "ice": _clean_ice(raw.get("ice")),
                    "ts": time.time(),
                }
            ]
        )
    ok = ev.wait(ANSWER_WAIT_S)
    with _lock:
        if owner:
            _waiters.pop(session, None)
            answer = _answers.pop(session, None)
            if answer:
                _done[session] = (time.monotonic(), answer)
            else:
                _late[session] = time.monotonic()
        else:
            kept = _done.get(session)
            answer = kept[1] if kept is not None else _answers.get(session)
    if not ok or not answer:
        return {"error": "desktop did not answer in time", "version": __version__, "protocol": PROTOCOL_VERSION}
    return _reply(answer)


def _prune(now: float) -> None:
    for table in (_done, _late):
        for sid in [k for k, v in table.items() if now - (v[0] if isinstance(v, tuple) else v) > RESUME_KEEP_S]:
            table.pop(sid, None)


def _reply(answer: dict[str, Any]) -> dict[str, Any]:
    if answer.get("error"):
        return {"error": str(answer["error"]), "version": __version__, "protocol": PROTOCOL_VERSION}
    return {
        "answer": answer.get("answer"),
        "fingerprint": str(answer.get("fingerprint") or ""),
        "version": __version__,
        "protocol": PROTOCOL_VERSION,
    }


def resolve_answer(session: str, answer: Any, *, error: str = "", fingerprint: str = "") -> bool:
    """Called by the page (``direct_rtc_answer``) once its answer is ready."""
    sid = str(session or "").strip()
    if not sid:
        return False
    clean = _clean_sdp(answer)
    if not error and (clean is None or clean["type"] != "answer"):
        error = "invalid answer"
    row = {"answer": clean, "error": error, "fingerprint": fingerprint}
    with _lock:
        ev = _waiters.get(sid)
        if ev is None:
            if sid in _late and not error:
                # Too late for that mailbox reply, in time for the viewer's retry.
                _late.pop(sid, None)
                _done[sid] = (time.monotonic(), row)
                return True
            return False
        _answers[sid] = row
        ev.set()
    return True


def note_report(payload: Any) -> None:
    """Remember the phone's last connect report for the presence heartbeat."""
    if not isinstance(payload, dict):
        return
    keep = {}
    for key in _LINK_KEYS + _HEALTH_KEYS:
        if key in payload:
            keep[key] = payload[key]
    if any(key in keep for key in _LINK_KEYS):
        # Health reports (quality, input lag, load, end) don't replace the link state the heartbeat shows.
        with _lock:
            _last_report.clear()
            _last_report.update({k: v for k, v in keep.items() if k in _LINK_KEYS}, at=time.time())
    try:
        from frontend import usage_stats

        usage_stats.remote_link(keep)
    except Exception:
        pass


def last_report() -> dict[str, Any]:
    with _lock:
        return dict(_last_report)


def pending_sessions() -> list[str]:
    with _lock:
        return sorted(_waiters)
