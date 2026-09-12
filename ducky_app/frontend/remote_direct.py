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
    ev = threading.Event()
    with _lock:
        _waiters[session] = ev
        _answers.pop(session, None)
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
        _waiters.pop(session, None)
        answer = _answers.pop(session, None)
    if not ok or not answer:
        return {"error": "desktop did not answer in time", "version": __version__, "protocol": PROTOCOL_VERSION}
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
    with _lock:
        ev = _waiters.get(sid)
        if ev is None:
            return False
        _answers[sid] = {"answer": clean, "error": error, "fingerprint": fingerprint}
        ev.set()
    return True


def note_report(payload: Any) -> None:
    """Remember the phone's last connect report for the presence heartbeat."""
    if not isinstance(payload, dict):
        return
    keep = {}
    for key in ("state", "reason", "connect_ms", "candidate", "attempt"):
        if key in payload:
            keep[key] = payload[key]
    keep["at"] = time.time()
    with _lock:
        _last_report.clear()
        _last_report.update(keep)


def last_report() -> dict[str, Any]:
    with _lock:
        return dict(_last_report)


def pending_sessions() -> list[str]:
    with _lock:
        return sorted(_waiters)
