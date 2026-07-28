"""Discord Gateway: bot Online + guild member/presence cache for the sidebar.

REST still owns channel messages (poller). This websocket:
  1. Keeps the bot green (IDENTIFY presence).
  2. With GUILDS | GUILD_MEMBERS | GUILD_PRESENCES, caches roles + members +
     status so the panel can render Discord's member list (hoisted roles,
     Offline accordion).

Requires Server Members Intent + Presence Intent ON in the Dev Portal.

Stdlib only (ssl + socket). Client frames are masked (RFC 6455).
"""

from __future__ import annotations

import base64
import json
import os
import socket
import ssl
import struct
import threading
import time
from typing import Any

from backend.discord import bots as _bots
from backend.discord.client import get_token as _client_get_token

_GATEWAY_HOST = "gateway.discord.gg"
_RECONNECT_MIN_S = 5.0
_RECONNECT_MAX_S = 120.0

# GUILDS alone is enough to hold an IDENTIFY session and show the bot Online.
# Privileged intents power the member sidebar; Discord closes with 4014 if they
# are requested without Dev Portal toggles — we fall back to GUILDS-only then.
_INTENTS_BASIC = 1 << 0  # GUILDS
_INTENTS_FULL = (1 << 0) | (1 << 1) | (1 << 8)  # GUILDS | GUILD_MEMBERS | GUILD_PRESENCES
_CLOSE_DISALLOWED_INTENTS = 4014
_PRESENCE_REFRESH_S = 240.0  # re-assert Online so Discord doesn't drop the dot

_ONLINE_STATUSES = frozenset({"online", "idle", "dnd"})

_lock = threading.Lock()
# bot_id -> set once that bot's presence thread has been spawned
_started_bots: set[str] = set()
# bot_id -> prefer GUILDS-only after a 4014 (privileged intents not enabled)
_basic_intents_bots: set[str] = set()
# bot_id -> Settings changed (show_offline etc.); session should PRESENCE_UPDATE now
_presence_bump: set[str] = set()
# bot_id -> Discord user id from READY (for optimistic Online in sidebar cache)
_bot_user_ids: dict[str, str] = {}
# (bot_id, guild_id) -> { roles, members, requested }
_cache: dict[tuple[str, str], dict[str, Any]] = {}


def desired_presence_status(bot_id: str) -> str:
    """Discord presence status for this bot: online unless show_offline is set."""
    profile = _bots.get_bot(bot_id)
    if profile is not None and bool(getattr(profile, "show_offline", False)):
        return "invisible"
    return "online"


def _presence_payload(bot_id: str) -> dict[str, Any]:
    return {
        "status": desired_presence_status(bot_id),
        "afk": False,
        "activities": [],
        "since": None,
    }


def _send_presence_update(sock: ssl.SSLSocket, bot_id: str) -> None:
    payload = _presence_payload(bot_id)
    _send_json(sock, {"op": 3, "d": payload})
    _mark_self_status(bot_id, str(payload.get("status") or "online"))


def _mark_self_status(bot_id: str, status: str) -> None:
    """Keep our own sidebar row in sync with the presence we advertise."""
    st = status if status in _ONLINE_STATUSES else "offline"
    if status == "invisible":
        st = "offline"
    with _lock:
        uid = _bot_user_ids.get(bot_id) or ""
        if not uid:
            return
        for (bid, _gid), slot in _cache.items():
            if bid != bot_id:
                continue
            members = slot.get("members")
            if not isinstance(members, dict):
                continue
            me = members.get(uid)
            if isinstance(me, dict):
                me["status"] = st
            else:
                members[uid] = {
                    "id": uid,
                    "username": "bot",
                    "global_name": None,
                    "nick": None,
                    "bot": True,
                    "roles": [],
                    "status": st,
                }


# --- Pure grouping (testable without a live gateway) --------------------------

def _display_name(m: dict[str, Any]) -> str:
    return str(m.get("nick") or m.get("global_name") or m.get("username") or "unknown")


def _highest_hoisted_role(
    role_ids: list[str], roles: dict[str, dict[str, Any]]
) -> dict[str, Any] | None:
    best: dict[str, Any] | None = None
    for rid in role_ids:
        r = roles.get(rid)
        if not r or not r.get("hoist"):
            continue
        if best is None or int(r.get("position", 0)) > int(best.get("position", 0)):
            best = r
    return best


def group_members(
    members: dict[str, dict[str, Any]],
    roles: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Discord sidebar layout: online by highest hoisted role, then Online, then Offline."""
    buckets: dict[str, list[dict[str, Any]]] = {}
    # role_id or "_online" / "_offline" -> meta for header
    meta: dict[str, dict[str, Any]] = {
        "_online": {"id": "online", "name": "Online", "position": -1, "color": 0},
        "_offline": {"id": "offline", "name": "Offline", "position": -2, "color": 0},
    }

    for m in members.values():
        status = str(m.get("status") or "offline")
        if status not in _ONLINE_STATUSES:
            status = "offline"
        name = _display_name(m)
        bot = bool(m.get("bot", False))
        uid = str(m.get("id") or "")
        role_ids = [str(r) for r in (m.get("roles") or [])]
        entry_color = 0
        if status == "offline":
            key = "_offline"
        else:
            hoist = _highest_hoisted_role(role_ids, roles)
            if hoist:
                key = str(hoist["id"])
                meta[key] = {
                    "id": key,
                    "name": str(hoist.get("name") or "Role"),
                    "position": int(hoist.get("position", 0)),
                    "color": int(hoist.get("color") or 0),
                }
                entry_color = int(hoist.get("color") or 0)
            else:
                key = "_online"
                # Colored by highest role (even non-hoisted) when present.
                colored = None
                for rid in role_ids:
                    r = roles.get(rid)
                    if not r:
                        continue
                    if colored is None or int(r.get("position", 0)) > int(colored.get("position", 0)):
                        colored = r
                if colored:
                    entry_color = int(colored.get("color") or 0)

        buckets.setdefault(key, []).append(
            {
                "id": uid,
                "name": name,
                "bot": bot,
                "status": status if status in _ONLINE_STATUSES else "offline",
                "color": entry_color or None,
            }
        )

    def sort_key(k: str) -> tuple[int, int, str]:
        if k == "_offline":
            return (2, 0, "")
        if k == "_online":
            return (1, 0, "")
        # Online role groups: higher position first.
        m = meta.get(k) or {}
        return (0, -int(m.get("position", 0)), str(m.get("name") or ""))

    groups: list[dict[str, Any]] = []
    for key in sorted(buckets.keys(), key=sort_key):
        people = buckets[key]
        people.sort(key=lambda p: (p["name"] or "").lower())
        info = meta.get(key) or {"id": key, "name": key}
        groups.append(
            {
                "id": info["id"],
                "name": info["name"],
                "count": len(people),
                "members": people,
            }
        )
    return groups


def snapshot(guild_id: str | None = None, *, bot_id: str | None = None) -> dict[str, Any]:
    """Grouped member list for the panel. Empty groups if cache is cold."""
    bid = (bot_id or _bots.DEFAULT_BOT_ID).strip() or _bots.DEFAULT_BOT_ID
    gid = (guild_id or _bots.get_guild_id(bid) or "").strip()
    with _lock:
        g = _cache.get((bid, gid)) if gid else None
        if not g:
            return {"ok": True, "guild_id": gid, "bot_id": bid, "groups": [], "ready": False}
        members = dict(g.get("members") or {})
        roles = dict(g.get("roles") or {})
    return {
        "ok": True,
        "guild_id": gid,
        "bot_id": bid,
        "groups": group_members(members, roles),
        "ready": bool(members),
    }


# --- Cache mutators -----------------------------------------------------------

def _guild_slot(bot_id: str, guild_id: str) -> dict[str, Any]:
    key = (bot_id, guild_id)
    slot = _cache.get(key)
    if slot is None:
        slot = {"roles": {}, "members": {}, "requested": False}
        _cache[key] = slot
    return slot


def _upsert_member(
    bot_id: str, guild_id: str, raw: dict[str, Any], status: str | None = None
) -> None:
    user = raw.get("user") or {}
    uid = str(user.get("id") or raw.get("id") or "")
    if not uid:
        return
    with _lock:
        slot = _guild_slot(bot_id, guild_id)
        prev = slot["members"].get(uid) or {}
        slot["members"][uid] = {
            "id": uid,
            "username": str(user.get("username") or prev.get("username") or ""),
            "global_name": str(user.get("global_name") or prev.get("global_name") or "") or None,
            "nick": (str(raw["nick"]) if raw.get("nick") else prev.get("nick")),
            "bot": bool(user.get("bot", prev.get("bot", False))),
            "roles": [str(r) for r in (raw.get("roles") if "roles" in raw else prev.get("roles") or [])],
            "status": status if status is not None else str(prev.get("status") or "offline"),
        }


def _set_roles(bot_id: str, guild_id: str, roles_raw: list[Any]) -> None:
    with _lock:
        slot = _guild_slot(bot_id, guild_id)
        roles: dict[str, dict[str, Any]] = {}
        for r in roles_raw or []:
            if not isinstance(r, dict):
                continue
            rid = str(r.get("id") or "")
            if not rid:
                continue
            roles[rid] = {
                "id": rid,
                "name": str(r.get("name") or "Role"),
                "position": int(r.get("position") or 0),
                "hoist": bool(r.get("hoist", False)),
                "color": int(r.get("color") or 0),
            }
        slot["roles"] = roles


def _set_status(
    bot_id: str,
    guild_id: str,
    user_id: str,
    status: str,
    user: dict[str, Any] | None = None,
) -> None:
    st = status if status in _ONLINE_STATUSES else "offline"
    with _lock:
        slot = _guild_slot(bot_id, guild_id)
        m = slot["members"].get(user_id)
        if m:
            m["status"] = st
            return
        if not user:
            return
        # Presence arrived before the member chunk — seed a minimal row.
        slot["members"][user_id] = {
            "id": user_id,
            "username": str(user.get("username") or ""),
            "global_name": str(user.get("global_name") or "") or None,
            "nick": None,
            "bot": bool(user.get("bot", False)),
            "roles": [],
            "status": st,
        }


def _remove_member(bot_id: str, guild_id: str, user_id: str) -> None:
    with _lock:
        slot = _cache.get((bot_id, guild_id))
        if slot:
            slot["members"].pop(user_id, None)


def _clear_cache(bot_id: str | None = None) -> None:
    with _lock:
        if bot_id is None:
            _cache.clear()
            return
        for key in list(_cache.keys()):
            if key[0] == bot_id:
                _cache.pop(key, None)


# --- RFC 6455 client framing -------------------------------------------------

def mask_frame(opcode: int, payload: bytes, mask: bytes) -> bytes:
    """Build one masked client frame (FIN set)."""
    frame = bytearray([0x80 | opcode])
    ln = len(payload)
    if ln < 126:
        frame.append(0x80 | ln)
    elif ln < 65536:
        frame.append(0x80 | 126)
        frame.extend(struct.pack("!H", ln))
    else:
        frame.append(0x80 | 127)
        frame.extend(struct.pack("!Q", ln))
    frame.extend(mask)
    frame.extend(b ^ mask[i % 4] for i, b in enumerate(payload))
    return bytes(frame)


def _send(sock: ssl.SSLSocket, opcode: int, payload: bytes) -> None:
    sock.sendall(mask_frame(opcode, payload, os.urandom(4)))


def _send_json(sock: ssl.SSLSocket, obj: dict) -> None:
    _send(sock, 0x1, json.dumps(obj, separators=(",", ":")).encode("utf-8"))


def _read_exact(sock: ssl.SSLSocket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("gateway closed")
        buf.extend(chunk)
    return bytes(buf)


def _read_message(sock: ssl.SSLSocket) -> tuple[int, bytes]:
    """One complete ws message (reassembling fragments): (opcode, payload)."""
    opcode = 0
    payload = bytearray()
    while True:
        h = _read_exact(sock, 2)
        fin = (h[0] & 0x80) != 0
        op = h[0] & 0x0F
        ln = h[1] & 0x7F
        if ln == 126:
            ln = struct.unpack("!H", _read_exact(sock, 2))[0]
        elif ln == 127:
            ln = struct.unpack("!Q", _read_exact(sock, 8))[0]
        # Server frames are unmasked (mask bit 0x80 of h[1] unset for Discord).
        payload.extend(_read_exact(sock, ln))
        if op != 0:
            opcode = op
        if fin:
            return opcode, bytes(payload)


# --- Gateway session ----------------------------------------------------------

def _connect() -> ssl.SSLSocket:
    raw = socket.create_connection((_GATEWAY_HOST, 443), timeout=15.0)
    sock = ssl.create_default_context().wrap_socket(raw, server_hostname=_GATEWAY_HOST)
    key = base64.b64encode(os.urandom(16)).decode()
    sock.sendall(
        (
            f"GET /?v=10&encoding=json HTTP/1.1\r\n"
            f"Host: {_GATEWAY_HOST}\r\n"
            "Upgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
        ).encode()
    )
    head = bytearray()
    while b"\r\n\r\n" not in head:
        chunk = sock.recv(1024)
        if not chunk:
            raise ConnectionError("gateway handshake closed")
        head.extend(chunk)
        if len(head) > 16384:
            raise ConnectionError("gateway handshake too large")
    if b" 101 " not in head.split(b"\r\n", 1)[0]:
        raise ConnectionError("gateway refused upgrade")
    return sock


def _request_members(sock: ssl.SSLSocket, guild_id: str) -> None:
    _send_json(
        sock,
        {
            "op": 8,
            "d": {
                "guild_id": guild_id,
                "query": "",
                "limit": 0,
                "presences": True,
            },
        },
    )


def _handle_dispatch(sock: ssl.SSLSocket, event: str, data: Any, *, bot_id: str) -> None:
    want = (_bots.get_guild_id(bot_id) or "").strip()
    if event == "GUILD_CREATE" and isinstance(data, dict):
        gid = str(data.get("id") or "")
        if want and gid != want:
            return
        if not gid:
            return
        _set_roles(bot_id, gid, list(data.get("roles") or []))
        for m in data.get("members") or []:
            if isinstance(m, dict):
                _upsert_member(bot_id, gid, m, status="offline")
        for p in data.get("presences") or []:
            if not isinstance(p, dict):
                continue
            u = p.get("user") or {}
            uid = str(u.get("id") or "")
            if uid:
                _set_status(
                    bot_id, gid, uid, str(p.get("status") or "offline"),
                    user=u if isinstance(u, dict) else None,
                )
        need_request = False
        with _lock:
            slot = _guild_slot(bot_id, gid)
            # GUILD_CREATE only embeds a partial member set — request the full roster once.
            if not slot["requested"]:
                slot["requested"] = True
                need_request = True
        if need_request:
            _request_members(sock, gid)
        return

    if event == "GUILD_MEMBERS_CHUNK" and isinstance(data, dict):
        gid = str(data.get("guild_id") or "")
        if want and gid != want:
            return
        for m in data.get("members") or []:
            if isinstance(m, dict):
                _upsert_member(bot_id, gid, m)
        for p in data.get("presences") or []:
            if not isinstance(p, dict):
                continue
            u = p.get("user") or {}
            uid = str(u.get("id") or "")
            if uid:
                _set_status(
                    bot_id, gid, uid, str(p.get("status") or "offline"),
                    user=u if isinstance(u, dict) else None,
                )
        return

    if event == "GUILD_MEMBER_ADD" and isinstance(data, dict):
        gid = str(data.get("guild_id") or "")
        if want and gid != want:
            return
        _upsert_member(bot_id, gid, data, status="offline")
        return

    if event == "GUILD_MEMBER_UPDATE" and isinstance(data, dict):
        gid = str(data.get("guild_id") or "")
        if want and gid != want:
            return
        _upsert_member(bot_id, gid, data)
        return

    if event == "GUILD_MEMBER_REMOVE" and isinstance(data, dict):
        gid = str(data.get("guild_id") or "")
        if want and gid != want:
            return
        uid = str((data.get("user") or {}).get("id") or "")
        if uid:
            _remove_member(bot_id, gid, uid)
        return

    if event == "PRESENCE_UPDATE" and isinstance(data, dict):
        gid = str(data.get("guild_id") or "")
        if want and gid != want:
            return
        u = data.get("user") or {}
        uid = str(u.get("id") or "")
        if uid and gid:
            _set_status(
                bot_id, gid, uid, str(data.get("status") or "offline"),
                user=u if isinstance(u, dict) else None,
            )
        return

    if event == "GUILD_ROLE_CREATE" and isinstance(data, dict):
        gid = str(data.get("guild_id") or "")
        role = data.get("role")
        if want and gid != want:
            return
        if isinstance(role, dict) and gid:
            with _lock:
                slot = _guild_slot(bot_id, gid)
                rid = str(role.get("id") or "")
                if rid:
                    slot["roles"][rid] = {
                        "id": rid,
                        "name": str(role.get("name") or "Role"),
                        "position": int(role.get("position") or 0),
                        "hoist": bool(role.get("hoist", False)),
                        "color": int(role.get("color") or 0),
                    }
        return

    if event == "GUILD_ROLE_UPDATE" and isinstance(data, dict):
        gid = str(data.get("guild_id") or "")
        role = data.get("role")
        if want and gid != want:
            return
        if isinstance(role, dict) and gid:
            with _lock:
                slot = _guild_slot(bot_id, gid)
                rid = str(role.get("id") or "")
                if rid:
                    slot["roles"][rid] = {
                        "id": rid,
                        "name": str(role.get("name") or "Role"),
                        "position": int(role.get("position") or 0),
                        "hoist": bool(role.get("hoist", False)),
                        "color": int(role.get("color") or 0),
                    }
        return

    if event == "GUILD_ROLE_DELETE" and isinstance(data, dict):
        gid = str(data.get("guild_id") or "")
        rid = str(data.get("role_id") or "")
        if want and gid != want:
            return
        if gid and rid:
            with _lock:
                slot = _cache.get((bot_id, gid))
                if slot:
                    slot["roles"].pop(rid, None)


def _session(token: str, bot_id: str, *, intents: int) -> int:
    """One connect→identify→heartbeat session.

    Returns Discord close code when the server closes (0 = clean/other exit).
    """
    close_code = 0
    sock = _connect()
    try:
        op, payload = _read_message(sock)
        hello = json.loads(payload)
        if hello.get("op") != 10:
            return 0
        interval_s = float(hello["d"]["heartbeat_interval"]) / 1000.0
        _send_json(
            sock,
            {
                "op": 2,
                "d": {
                    "token": token,
                    "intents": intents,
                    "properties": {"os": "windows", "browser": "uefn-ducky", "device": "uefn-ducky"},
                    "presence": _presence_payload(bot_id),
                },
            },
        )
        seq: int | None = None
        next_beat = time.monotonic() + interval_s * 0.5
        next_presence = time.monotonic() + 2.0  # assert Online soon after IDENTIFY
        sock.settimeout(1.0)
        while True:
            if _client_get_token(bot_id) != token:
                return 0  # token changed/cleared — reconnect (or exit) with the new one
            profile = _bots.get_bot(bot_id)
            if profile is not None and not profile.enabled:
                return 0
            now = time.monotonic()
            if now >= next_beat:
                _send_json(sock, {"op": 1, "d": seq})
                next_beat = now + interval_s
            with _lock:
                bumped = bot_id in _presence_bump
                if bumped:
                    _presence_bump.discard(bot_id)
            if now >= next_presence or bumped:
                try:
                    _send_presence_update(sock, bot_id)
                except OSError:
                    return close_code
                next_presence = now + _PRESENCE_REFRESH_S
            try:
                op, payload = _read_message(sock)
            except (TimeoutError, socket.timeout):
                continue
            if op == 0x8:  # ws close
                if len(payload) >= 2:
                    close_code = struct.unpack("!H", payload[:2])[0]
                return close_code
            if op == 0x9:  # ws ping → pong
                _send(sock, 0xA, payload)
                continue
            if op not in (0x1, 0x2):
                continue
            try:
                msg = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if msg.get("s") is not None:
                seq = msg["s"]
            gw_op = msg.get("op")
            if gw_op == 1:  # server asked for an immediate heartbeat
                _send_json(sock, {"op": 1, "d": seq})
                next_beat = time.monotonic() + interval_s
            elif gw_op in (7, 9):  # reconnect / invalid session
                return close_code
            elif gw_op == 0:
                event = str(msg.get("t") or "")
                # Messages stay on the poller; ignore MESSAGE_* here.
                if event.startswith("MESSAGE_"):
                    continue
                if event == "READY":
                    data = msg.get("d") if isinstance(msg.get("d"), dict) else {}
                    user = data.get("user") if isinstance(data.get("user"), dict) else {}
                    uid = str(user.get("id") or "").strip()
                    if uid:
                        with _lock:
                            _bot_user_ids[bot_id] = uid
                    try:
                        _send_presence_update(sock, bot_id)
                    except OSError:
                        return close_code
                    next_presence = time.monotonic() + _PRESENCE_REFRESH_S
                    continue
                _handle_dispatch(sock, event, msg.get("d"), bot_id=bot_id)
    finally:
        try:
            sock.close()
        except OSError:
            pass
        _clear_cache(bot_id)
    return close_code


def _run(bot_id: str) -> None:
    backoff = _RECONNECT_MIN_S
    while True:
        # Disabled bots park until re-enabled.
        profile = _bots.get_bot(bot_id)
        if profile is not None and not profile.enabled:
            time.sleep(30.0)
            continue
        token = _client_get_token(bot_id)
        if not token:
            time.sleep(30.0)
            continue
        with _lock:
            use_basic = bot_id in _basic_intents_bots
        intents = _INTENTS_BASIC if use_basic else _INTENTS_FULL
        started = time.monotonic()
        close_code = 0
        try:
            close_code = _session(token, bot_id, intents=intents)
        except Exception:
            close_code = 0
        if close_code == _CLOSE_DISALLOWED_INTENTS and not use_basic:
            # Privileged intents not enabled in Dev Portal — keep Online with GUILDS.
            with _lock:
                _basic_intents_bots.add(bot_id)
            backoff = _RECONNECT_MIN_S
            continue
        # A session that held for a while earns a fresh (short) backoff.
        backoff = _RECONNECT_MIN_S if time.monotonic() - started > 60.0 else min(backoff * 2, _RECONNECT_MAX_S)
        time.sleep(backoff)


def bump_presence(bot_id: str | None = None) -> None:
    """Ask the live gateway session to re-send Online/invisible (e.g. after Save)."""
    bid = (bot_id or _bots.DEFAULT_BOT_ID).strip() or _bots.DEFAULT_BOT_ID
    with _lock:
        _presence_bump.add(bid)
    ensure_started(bid)


def ensure_started(bot_id: str | None = None) -> None:
    """Start the presence thread for one bot (no-op if already running)."""
    bid = (bot_id or _bots.DEFAULT_BOT_ID).strip() or _bots.DEFAULT_BOT_ID
    with _lock:
        if bid in _started_bots:
            return
        _started_bots.add(bid)
    threading.Thread(target=_run, args=(bid,), daemon=True, name=f"discord-presence-{bid}").start()


def ensure_all_enabled() -> None:
    """Start presence for every enabled bot that has a token."""
    for b in _bots.enabled_bots():
        if _client_get_token(b.id):
            ensure_started(b.id)


if __name__ == "__main__":  # pragma: no cover - offline self-check
    # Mask/unmask roundtrip: client-masked frame decodes back to the payload.
    body = b'{"op":1,"d":null}'
    frame = mask_frame(0x1, body, b"\x01\x02\x03\x04")
    assert frame[0] == 0x81 and (frame[1] & 0x80), "FIN+text, masked"
    ln = frame[1] & 0x7F
    mask, data = frame[2:6], frame[6 : 6 + ln]
    assert bytes(b ^ mask[i % 4] for i, b in enumerate(data)) == body
    # Long-payload length encoding.
    big = mask_frame(0x1, b"x" * 300, b"\0\0\0\0")
    assert (big[1] & 0x7F) == 126 and struct.unpack("!H", big[2:4])[0] == 300

    # Default presence is Online; show_offline flips to invisible.
    class _P:
        show_offline = False

    class _POff:
        show_offline = True

    assert desired_presence_status("missing") == "online"
    _orig = _bots.get_bot
    try:
        _bots.get_bot = lambda _id: _P()  # type: ignore[assignment]
        assert desired_presence_status("x") == "online"
        assert _presence_payload("x")["status"] == "online"
        _bots.get_bot = lambda _id: _POff()  # type: ignore[assignment]
        assert desired_presence_status("x") == "invisible"
    finally:
        _bots.get_bot = _orig  # type: ignore[assignment]

    # Grouping: hoisted Admin online, plain Online, Offline accordion bucket.
    roles = {
        "10": {"id": "10", "name": "Admin", "position": 5, "hoist": True, "color": 0xFF0000},
        "20": {"id": "20", "name": "Member", "position": 1, "hoist": False, "color": 0},
    }
    members = {
        "a": {
            "id": "a",
            "username": "alice",
            "global_name": "Alice",
            "nick": None,
            "bot": False,
            "roles": ["10"],
            "status": "online",
        },
        "b": {
            "id": "b",
            "username": "bob",
            "global_name": "Bob",
            "nick": None,
            "bot": False,
            "roles": ["20"],
            "status": "idle",
        },
        "c": {
            "id": "c",
            "username": "carol",
            "global_name": "Carol",
            "nick": None,
            "bot": True,
            "roles": [],
            "status": "offline",
        },
    }
    groups = group_members(members, roles)
    assert [g["id"] for g in groups] == ["10", "online", "offline"], groups
    assert groups[0]["name"] == "Admin" and groups[0]["members"][0]["name"] == "Alice"
    assert groups[1]["members"][0]["name"] == "Bob" and groups[1]["members"][0]["status"] == "idle"
    assert groups[2]["members"][0]["bot"] is True
    print("presence self-check ok")
    if _client_get_token(_bots.DEFAULT_BOT_ID):
        print("connecting live for 10s…")
        ensure_started(_bots.DEFAULT_BOT_ID)
        time.sleep(10)
        print("snapshot:", snapshot(bot_id=_bots.DEFAULT_BOT_ID))
        print("check the bot's dot in Discord")
