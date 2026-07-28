"""Shared Discord REST client — the only place that talks to Discord.

The panel poller, the panel send, and the agent tools all call these helpers.
Token + guild resolve from multi-bot profiles (``backend.discord.bots``); pass
``bot_id`` (default ``"default"``). Callers never handle credentials. Every
request goes through one wrapper that adds auth, decodes JSON, and retries once
on a 429 rate-limit.

No `discord.py` — REST only. Message Content Intent must be ON in the Discord
Developer Portal or `content` comes back empty.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any

from backend.discord import bots as _bots

_API = "https://discord.com/api/v10"
_TIMEOUT = 8.0
DEFAULT_BOT_ID = _bots.DEFAULT_BOT_ID
# Channel types we surface to agents / chat.
_TEXT_CHANNEL_TYPES = {0, 5}  # GUILD_TEXT, GUILD_ANNOUNCEMENT
_LISTED_CHANNEL_TYPES = {0, 2, 4, 5}  # text, voice, category, announcement
_CHANNEL_TYPE_NAMES = {0: "text", 2: "voice", 4: "category", 5: "announcement"}
_TYPE_NAME_TO_INT = {v: k for k, v in _CHANNEL_TYPE_NAMES.items()}


def get_token(bot_id: str | None = None) -> str | None:
    return _bots.get_token(bot_id)


def get_guild_id(bot_id: str | None = None) -> str | None:
    return _bots.get_guild_id(bot_id)


class DiscordError(Exception):
    """Raised for a failed request. Callers in loops should catch and degrade."""


def _request(
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    *,
    bot_id: str | None = None,
) -> Any:
    """One HTTP call to Discord: auth header, JSON in/out, single 429 retry.

    Raises DiscordError on any failure (no token, HTTP error, timeout) — never
    leaks a raw urllib exception to callers. Each bot_id uses its own token
    (separate Discord rate-limit bucket).
    """
    bid = bot_id or DEFAULT_BOT_ID
    token = get_token(bid)
    if not token:
        raise DiscordError("No Discord bot token set")

    url = f"{_API}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {
        "Authorization": f"Bot {token}",
        "User-Agent": "UEFN-Ducky (https://duckyos.org, 1.0)",
        "Content-Type": "application/json",
    }

    for attempt in range(2):  # ponytail: one retry covers a single 429; not a full backoff ladder
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt == 0:
                retry_after = 1.0
                try:
                    retry_after = float(json.loads(e.read()).get("retry_after", 1.0))
                except Exception:
                    pass
                time.sleep(min(retry_after, 5.0))
                continue
            detail = ""
            try:
                detail = e.read().decode("utf-8", "replace")[:200]
            except Exception:
                pass
            raise DiscordError(f"Discord {e.code}: {detail or e.reason}") from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise DiscordError(f"Discord request failed: {e}") from e
        except json.JSONDecodeError as e:
            raise DiscordError("Discord returned malformed JSON") from e
    raise DiscordError("Discord rate-limited (retry exhausted)")


def _require_guild(guild_id: str | None = None, *, bot_id: str | None = None) -> str:
    gid = (guild_id or get_guild_id(bot_id) or "").strip()
    if not gid:
        raise DiscordError("No Discord server (guild) id set")
    return gid


def _normalize_message(m: dict[str, Any]) -> dict[str, Any]:
    author = m.get("author") or {}
    return {
        "id": str(m.get("id", "")),
        "author": str(author.get("global_name") or author.get("username") or "unknown"),
        "author_id": str(author.get("id", "")),
        "bot": bool(author.get("bot", False)),
        "content": str(m.get("content") or ""),
        "timestamp": str(m.get("timestamp") or ""),
    }


def _normalize_channel(c: dict[str, Any]) -> dict[str, Any]:
    ctype = int(c.get("type", -1))
    out: dict[str, Any] = {
        "id": str(c.get("id", "")),
        "name": str(c.get("name") or "channel"),
        "type": ctype,
        "type_name": _CHANNEL_TYPE_NAMES.get(ctype, str(ctype)),
        "position": int(c.get("position", 0)),
    }
    parent = c.get("parent_id")
    if parent:
        out["parent_id"] = str(parent)
    topic = c.get("topic")
    if topic:
        out["topic"] = str(topic)
    if "nsfw" in c:
        out["nsfw"] = bool(c.get("nsfw"))
    if "rate_limit_per_user" in c:
        out["rate_limit_per_user"] = int(c.get("rate_limit_per_user") or 0)
    return out


def _normalize_role(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(r.get("id", "")),
        "name": str(r.get("name") or "role"),
        "position": int(r.get("position", 0)),
        "permissions": str(r.get("permissions") or "0"),
        "mentionable": bool(r.get("mentionable", False)),
        "hoist": bool(r.get("hoist", False)),
        "color": int(r.get("color") or 0),
        "managed": bool(r.get("managed", False)),
    }


def _normalize_member(m: dict[str, Any]) -> dict[str, Any]:
    user = m.get("user") or {}
    return {
        "id": str(user.get("id", "")),
        "username": str(user.get("global_name") or user.get("username") or "unknown"),
        "nick": str(m.get("nick") or "") or None,
        "roles": [str(r) for r in (m.get("roles") or [])],
        "bot": bool(user.get("bot", False)),
    }


def _resolve_channel_type(channel_type: int | str | None) -> int:
    if channel_type is None:
        return 0
    if isinstance(channel_type, int):
        return channel_type
    raw = str(channel_type).strip().lower()
    if raw.isdigit():
        return int(raw)
    if raw in _TYPE_NAME_TO_INT:
        return _TYPE_NAME_TO_INT[raw]
    raise DiscordError(f"Unknown channel type: {channel_type}")


def bot_status(*, bot_id: str | None = None) -> dict[str, Any]:
    """Connection test — is the token valid and the bot reachable? The Settings 'Test' button."""
    bid = bot_id or DEFAULT_BOT_ID
    try:
        me = _request("GET", "/users/@me", bot_id=bid)
    except DiscordError as e:
        return {"ok": False, "error": str(e), "bot_id": bid}
    name = str((me or {}).get("username") or "bot")
    return {"ok": True, "bot_name": name, "guild_id": get_guild_id(bid) or "", "bot_id": bid}


def application_id(*, bot_id: str | None = None) -> str:
    """The bot's application id — for deep-linking its Dev Portal edit page."""
    app = _request("GET", "/oauth2/applications/@me", bot_id=bot_id or DEFAULT_BOT_ID)
    return str((app or {}).get("id") or "")


def list_channels(
    guild_id: str | None = None, *, text_only: bool = False, bot_id: str | None = None
) -> list[dict[str, Any]]:
    """Channels of the guild. text_only=True keeps the Group Chat picker on text/announcement."""
    bid = bot_id or DEFAULT_BOT_ID
    gid = _require_guild(guild_id, bot_id=bid)
    channels = _request("GET", f"/guilds/{gid}/channels", bot_id=bid) or []
    allowed = _TEXT_CHANNEL_TYPES if text_only else _LISTED_CHANNEL_TYPES
    out = [_normalize_channel(c) for c in channels if int(c.get("type", -1)) in allowed]
    out.sort(key=lambda c: (c.get("type") != 4, c.get("position", 0), c.get("name", "")))
    return out


def create_channel(
    name: str,
    *,
    channel_type: int | str = 0,
    parent_id: str | None = None,
    topic: str | None = None,
    position: int | None = None,
    nsfw: bool | None = None,
    rate_limit_per_user: int | None = None,
    guild_id: str | None = None,
    bot_id: str | None = None,
) -> dict[str, Any]:
    """Create a guild channel or category."""
    bid = bot_id or DEFAULT_BOT_ID
    gid = _require_guild(guild_id, bot_id=bid)
    body: dict[str, Any] = {
        "name": (name or "").strip(),
        "type": _resolve_channel_type(channel_type),
    }
    if not body["name"]:
        raise DiscordError("Channel name is empty")
    if parent_id:
        body["parent_id"] = parent_id.strip()
    if topic is not None:
        body["topic"] = topic
    if position is not None:
        body["position"] = int(position)
    if nsfw is not None:
        body["nsfw"] = bool(nsfw)
    if rate_limit_per_user is not None:
        body["rate_limit_per_user"] = int(rate_limit_per_user)
    created = _request("POST", f"/guilds/{gid}/channels", body, bot_id=bid) or {}
    return _normalize_channel(created)


def edit_channel(
    channel_id: str,
    *,
    name: str | None = None,
    topic: str | None = None,
    parent_id: str | None = None,
    position: int | None = None,
    nsfw: bool | None = None,
    rate_limit_per_user: int | None = None,
    bot_id: str | None = None,
) -> dict[str, Any]:
    """Rename / move / retopic a channel or category."""
    bid = bot_id or DEFAULT_BOT_ID
    cid = (channel_id or "").strip()
    if not cid:
        raise DiscordError("No channel id")
    body: dict[str, Any] = {}
    if name is not None:
        body["name"] = name.strip()
    if topic is not None:
        body["topic"] = topic
    if parent_id is not None:
        body["parent_id"] = parent_id.strip() or None
    if position is not None:
        body["position"] = int(position)
    if nsfw is not None:
        body["nsfw"] = bool(nsfw)
    if rate_limit_per_user is not None:
        body["rate_limit_per_user"] = int(rate_limit_per_user)
    if not body:
        raise DiscordError("Nothing to edit")
    updated = _request("PATCH", f"/channels/{cid}", body, bot_id=bid) or {}
    return _normalize_channel(updated)


def delete_channel(channel_id: str, *, bot_id: str | None = None) -> dict[str, Any]:
    """Delete a channel or category."""
    cid = (channel_id or "").strip()
    if not cid:
        raise DiscordError("No channel id")
    _request("DELETE", f"/channels/{cid}", bot_id=bot_id or DEFAULT_BOT_ID)
    return {"ok": True, "id": cid, "deleted": True}


def edit_channel_permissions(
    channel_id: str,
    overwrite_id: str,
    *,
    allow: str | int = 0,
    deny: str | int = 0,
    overwrite_type: int = 0,
    bot_id: str | None = None,
) -> dict[str, Any]:
    """Set a permission overwrite (type 0 = role, 1 = member)."""
    cid = (channel_id or "").strip()
    oid = (overwrite_id or "").strip()
    if not cid or not oid:
        raise DiscordError("channel_id and overwrite_id are required")
    body = {
        "type": int(overwrite_type),
        "allow": str(allow),
        "deny": str(deny),
    }
    _request("PUT", f"/channels/{cid}/permissions/{oid}", body, bot_id=bot_id or DEFAULT_BOT_ID)
    return {"ok": True, "channel_id": cid, "overwrite_id": oid, **body}


def delete_channel_permissions(
    channel_id: str, overwrite_id: str, *, bot_id: str | None = None
) -> dict[str, Any]:
    """Remove a permission overwrite."""
    cid = (channel_id or "").strip()
    oid = (overwrite_id or "").strip()
    if not cid or not oid:
        raise DiscordError("channel_id and overwrite_id are required")
    _request("DELETE", f"/channels/{cid}/permissions/{oid}", bot_id=bot_id or DEFAULT_BOT_ID)
    return {"ok": True, "channel_id": cid, "overwrite_id": oid, "deleted": True}


def list_roles(guild_id: str | None = None, *, bot_id: str | None = None) -> list[dict[str, Any]]:
    bid = bot_id or DEFAULT_BOT_ID
    gid = _require_guild(guild_id, bot_id=bid)
    roles = _request("GET", f"/guilds/{gid}/roles", bot_id=bid) or []
    out = [_normalize_role(r) for r in roles]
    out.sort(key=lambda r: (-r.get("position", 0), r.get("name", "")))
    return out


def create_role(
    name: str,
    *,
    permissions: str | int | None = None,
    color: int | None = None,
    hoist: bool | None = None,
    mentionable: bool | None = None,
    guild_id: str | None = None,
    bot_id: str | None = None,
) -> dict[str, Any]:
    bid = bot_id or DEFAULT_BOT_ID
    gid = _require_guild(guild_id, bot_id=bid)
    body: dict[str, Any] = {"name": (name or "").strip()}
    if not body["name"]:
        raise DiscordError("Role name is empty")
    if permissions is not None:
        body["permissions"] = str(permissions)
    if color is not None:
        body["color"] = int(color)
    if hoist is not None:
        body["hoist"] = bool(hoist)
    if mentionable is not None:
        body["mentionable"] = bool(mentionable)
    created = _request("POST", f"/guilds/{gid}/roles", body, bot_id=bid) or {}
    return _normalize_role(created)


def edit_role(
    role_id: str,
    *,
    name: str | None = None,
    permissions: str | int | None = None,
    color: int | None = None,
    hoist: bool | None = None,
    mentionable: bool | None = None,
    guild_id: str | None = None,
    bot_id: str | None = None,
) -> dict[str, Any]:
    bid = bot_id or DEFAULT_BOT_ID
    gid = _require_guild(guild_id, bot_id=bid)
    rid = (role_id or "").strip()
    if not rid:
        raise DiscordError("No role id")
    body: dict[str, Any] = {}
    if name is not None:
        body["name"] = name.strip()
    if permissions is not None:
        body["permissions"] = str(permissions)
    if color is not None:
        body["color"] = int(color)
    if hoist is not None:
        body["hoist"] = bool(hoist)
    if mentionable is not None:
        body["mentionable"] = bool(mentionable)
    if not body:
        raise DiscordError("Nothing to edit")
    updated = _request("PATCH", f"/guilds/{gid}/roles/{rid}", body, bot_id=bid) or {}
    return _normalize_role(updated)


def delete_role(role_id: str, *, guild_id: str | None = None, bot_id: str | None = None) -> dict[str, Any]:
    bid = bot_id or DEFAULT_BOT_ID
    gid = _require_guild(guild_id, bot_id=bid)
    rid = (role_id or "").strip()
    if not rid:
        raise DiscordError("No role id")
    _request("DELETE", f"/guilds/{gid}/roles/{rid}", bot_id=bid)
    return {"ok": True, "id": rid, "deleted": True}


def list_guild_members(
    *,
    limit: int = 100,
    after: str | None = None,
    guild_id: str | None = None,
    bot_id: str | None = None,
) -> list[dict[str, Any]]:
    """REST member list (paginated). Requires Server Members Intent."""
    bid = bot_id or DEFAULT_BOT_ID
    gid = _require_guild(guild_id, bot_id=bid)
    q = f"?limit={max(1, min(int(limit), 1000))}"
    if after:
        q += f"&after={urllib.parse.quote(after.strip())}"
    members = _request("GET", f"/guilds/{gid}/members{q}", bot_id=bid) or []
    return [_normalize_member(m) for m in members]


def add_role(
    user_id: str, role_id: str, *, guild_id: str | None = None, bot_id: str | None = None
) -> dict[str, Any]:
    bid = bot_id or DEFAULT_BOT_ID
    gid = _require_guild(guild_id, bot_id=bid)
    uid = (user_id or "").strip()
    rid = (role_id or "").strip()
    if not uid or not rid:
        raise DiscordError("user_id and role_id are required")
    _request("PUT", f"/guilds/{gid}/members/{uid}/roles/{rid}", bot_id=bid)
    return {"ok": True, "user_id": uid, "role_id": rid, "added": True}


def remove_role(
    user_id: str, role_id: str, *, guild_id: str | None = None, bot_id: str | None = None
) -> dict[str, Any]:
    bid = bot_id or DEFAULT_BOT_ID
    gid = _require_guild(guild_id, bot_id=bid)
    uid = (user_id or "").strip()
    rid = (role_id or "").strip()
    if not uid or not rid:
        raise DiscordError("user_id and role_id are required")
    _request("DELETE", f"/guilds/{gid}/members/{uid}/roles/{rid}", bot_id=bid)
    return {"ok": True, "user_id": uid, "role_id": rid, "removed": True}


def edit_member(
    user_id: str,
    *,
    nick: str | None = None,
    communication_disabled_until: str | None = None,
    guild_id: str | None = None,
    bot_id: str | None = None,
) -> dict[str, Any]:
    """Edit nick and/or timeout. Pass nick="" to clear; timeout ISO8601 or None to clear."""
    bid = bot_id or DEFAULT_BOT_ID
    gid = _require_guild(guild_id, bot_id=bid)
    uid = (user_id or "").strip()
    if not uid:
        raise DiscordError("No user id")
    body: dict[str, Any] = {}
    if nick is not None:
        body["nick"] = nick
    if communication_disabled_until is not None:
        body["communication_disabled_until"] = communication_disabled_until or None
    if not body:
        raise DiscordError("Nothing to edit")
    updated = _request("PATCH", f"/guilds/{gid}/members/{uid}", body, bot_id=bid) or {}
    return _normalize_member(updated) if updated.get("user") else {"ok": True, "user_id": uid, **body}


def timeout_member(
    user_id: str,
    *,
    minutes: int = 10,
    guild_id: str | None = None,
    bot_id: str | None = None,
) -> dict[str, Any]:
    """Timeout a member for N minutes (0 clears the timeout)."""
    mins = max(0, int(minutes))
    until: str | None
    if mins <= 0:
        until = None
    else:
        until = (datetime.now(timezone.utc) + timedelta(minutes=mins)).isoformat().replace("+00:00", "Z")
    return edit_member(user_id, communication_disabled_until=until, guild_id=guild_id, bot_id=bot_id)


def kick_member(
    user_id: str, *, reason: str | None = None, guild_id: str | None = None, bot_id: str | None = None
) -> dict[str, Any]:
    bid = bot_id or DEFAULT_BOT_ID
    gid = _require_guild(guild_id, bot_id=bid)
    uid = (user_id or "").strip()
    if not uid:
        raise DiscordError("No user id")
    path = f"/guilds/{gid}/members/{uid}"
    if reason:
        # Discord accepts X-Audit-Log-Reason via header; we keep it simple in body-less DELETE.
        path += f"?reason={urllib.parse.quote(reason[:512])}"
    _request("DELETE", path, bot_id=bid)
    return {"ok": True, "user_id": uid, "kicked": True}


def ban_member(
    user_id: str,
    *,
    delete_message_seconds: int = 0,
    reason: str | None = None,
    guild_id: str | None = None,
    bot_id: str | None = None,
) -> dict[str, Any]:
    bid = bot_id or DEFAULT_BOT_ID
    gid = _require_guild(guild_id, bot_id=bid)
    uid = (user_id or "").strip()
    if not uid:
        raise DiscordError("No user id")
    body: dict[str, Any] = {
        "delete_message_seconds": max(0, min(int(delete_message_seconds), 604800)),
    }
    if reason:
        body["reason"] = reason[:512]
    _request("PUT", f"/guilds/{gid}/bans/{uid}", body, bot_id=bid)
    return {"ok": True, "user_id": uid, "banned": True}


def fetch_messages(
    channel_id: str, after: str | None = None, limit: int = 50, *, bot_id: str | None = None
) -> list[dict[str, Any]]:
    """Messages for a channel, oldest-first. Pass `after` (a message id) to get only newer ones."""
    cid = (channel_id or "").strip()
    if not cid:
        raise DiscordError("No channel id")
    q = f"?limit={max(1, min(limit, 100))}"
    if after:
        q += f"&after={after}"
    msgs = _request("GET", f"/channels/{cid}/messages{q}", bot_id=bot_id or DEFAULT_BOT_ID) or []
    out = [_normalize_message(m) for m in msgs]
    # Discord returns newest-first; the UI and agents want chronological order.
    out.sort(key=lambda m: int(m["id"] or 0))
    return out


def send_message(channel_id: str, text: str, *, bot_id: str | None = None) -> dict[str, Any]:
    """Post a message to a channel as the bot. Returns the created message (normalized)."""
    cid = (channel_id or "").strip()
    body = (text or "").strip()
    if not cid:
        raise DiscordError("No channel id")
    if not body:
        raise DiscordError("Message is empty")
    created = _request(
        "POST", f"/channels/{cid}/messages", {"content": body[:2000]}, bot_id=bot_id or DEFAULT_BOT_ID
    )
    return _normalize_message(created or {})


def edit_message(
    channel_id: str, message_id: str, text: str, *, bot_id: str | None = None
) -> dict[str, Any]:
    cid = (channel_id or "").strip()
    mid = (message_id or "").strip()
    body = (text or "").strip()
    if not cid or not mid:
        raise DiscordError("channel_id and message_id are required")
    if not body:
        raise DiscordError("Message is empty")
    updated = _request(
        "PATCH",
        f"/channels/{cid}/messages/{mid}",
        {"content": body[:2000]},
        bot_id=bot_id or DEFAULT_BOT_ID,
    )
    return _normalize_message(updated or {})


def delete_message(
    channel_id: str, message_id: str, *, bot_id: str | None = None
) -> dict[str, Any]:
    cid = (channel_id or "").strip()
    mid = (message_id or "").strip()
    if not cid or not mid:
        raise DiscordError("channel_id and message_id are required")
    _request("DELETE", f"/channels/{cid}/messages/{mid}", bot_id=bot_id or DEFAULT_BOT_ID)
    return {"ok": True, "channel_id": cid, "message_id": mid, "deleted": True}


def create_invite(
    channel_id: str,
    *,
    max_age: int = 86400,
    max_uses: int = 0,
    temporary: bool = False,
    unique: bool = False,
    bot_id: str | None = None,
) -> dict[str, Any]:
    cid = (channel_id or "").strip()
    if not cid:
        raise DiscordError("No channel id")
    body = {
        "max_age": max(0, int(max_age)),
        "max_uses": max(0, int(max_uses)),
        "temporary": bool(temporary),
        "unique": bool(unique),
    }
    invite = _request("POST", f"/channels/{cid}/invites", body, bot_id=bot_id or DEFAULT_BOT_ID) or {}
    code = str(invite.get("code") or "")
    return {
        "ok": True,
        "code": code,
        "url": f"https://discord.gg/{code}" if code else "",
        "channel_id": cid,
        "max_age": body["max_age"],
        "max_uses": body["max_uses"],
    }


if __name__ == "__main__":  # pragma: no cover - manual live/offline self-check
    # Offline check: normalize is pure and must not crash on sparse payloads.
    n = _normalize_message({"id": "42", "author": {"username": "ada"}, "content": "hi"})
    assert n["id"] == "42" and n["author"] == "ada" and n["content"] == "hi", n
    assert _normalize_message({})["author"] == "unknown"
    # Ordering: fetch sorts ascending by id — verify the sort key directly.
    sample = [{"id": "3"}, {"id": "1"}, {"id": "2"}]
    sample.sort(key=lambda m: int(m["id"]))
    assert [m["id"] for m in sample] == ["1", "2", "3"]
    ch = _normalize_channel({"id": "1", "name": "general", "type": 0, "position": 2, "parent_id": "9"})
    assert ch["type_name"] == "text" and ch["parent_id"] == "9"
    assert _resolve_channel_type("category") == 4
    print("offline self-check ok")
    # Live check (only if a token is configured):
    if get_token():
        print("bot_status:", bot_status())
