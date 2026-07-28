"""Agent tools for Discord — chat + server admin via the shared REST client.

Thin wrappers over backend.discord.client (same code the Group Chat panel uses).
Configure the bot token + server id in Settings → Discord Bot first.

Registered by the Discord UEFN plugin (not imported from tools/__init__.py).
Secrets stay in credentials.dat via set_key — never in the plugin zip.
Discord itself enforces what the bot token is allowed to do.
"""

from __future__ import annotations

from typing import Any

from backend.discord import client
from backend.discord.client import DiscordError
from backend.json_util import tool_json
from backend.server import mcp

_MAX_READ = 100


def _require_plugin() -> None:
    from backend.uefn_plugins.host import is_plugin_enabled, uefn_agent_tools_allowed

    if not is_plugin_enabled("discord"):
        raise ValueError("Discord plugin is disabled — enable it in Settings → Store")
    if not uefn_agent_tools_allowed("discord"):
        raise ValueError(
            "Discord tools are off for this ducky — enable them under Tools & MCPs"
        )


def _call(fn, *args: Any, **kwargs: Any) -> Any:
    _require_plugin()
    try:
        return fn(*args, **kwargs)
    except DiscordError as e:
        raise ValueError(str(e)) from e


# --- Bots / connection -------------------------------------------------------


@mcp.tool()
def discord_list_bots(query: str = "", pretty: bool = False) -> str:
    """List configured Discord bots in UEFN-Ducky (id, label, Discord username, prefix, guild).

    Optional ``query`` filters by id/label/post_as/prefix/live username (e.g. \"bob\").
    Use this to find bots like Ducky BoB before channel/admin tools.
    """
    from backend.discord import bots as discord_bots

    _require_plugin()
    q = (query or "").strip().lower()
    rows: list[dict[str, Any]] = []
    for profile in discord_bots.list_bots():
        status = client.bot_status(bot_id=profile.id)
        live_name = str(status.get("bot_name") or "") if status.get("ok") else ""
        row = {
            "id": profile.id,
            "label": profile.label,
            "post_as": profile.post_as,
            "prefix": profile.prefix,
            "guild_id": profile.guild_id,
            "channel_id": profile.channel_id,
            "enabled": profile.enabled,
            "show_offline": bool(getattr(profile, "show_offline", False)),
            "connected": bool(status.get("ok")),
            "bot_username": live_name,
            "error": None if status.get("ok") else str(status.get("error") or ""),
        }
        if q:
            hay = " ".join(
                str(row.get(k) or "")
                for k in ("id", "label", "post_as", "prefix", "bot_username")
            ).lower()
            if q not in hay and not all(tok in hay for tok in q.split()):
                continue
        rows.append(row)
    return tool_json({"query": query, "bots": rows, "count": len(rows)}, pretty=pretty)


# --- Chat (existing) ---------------------------------------------------------


@mcp.tool()
def discord_list_channels(pretty: bool = False) -> str:
    """List channels and categories of the configured Discord server (id, name, type, parent)."""
    channels = _call(client.list_channels, text_only=False)
    return tool_json({"channels": channels}, pretty=pretty)


@mcp.tool()
def discord_read_channel(channel_id: str, limit: int = 50, pretty: bool = False) -> str:
    """Read recent messages from a Discord channel (oldest-first) to summarize or analyze them."""
    messages = _call(
        client.fetch_messages,
        channel_id.strip(),
        limit=max(1, min(int(limit), _MAX_READ)),
    )
    return tool_json({"channel_id": channel_id.strip(), "messages": messages}, pretty=pretty)


@mcp.tool()
def discord_send_message(channel_id: str, text: str, pretty: bool = False) -> str:
    """Post a message to a Discord channel as the bot. Use only when the user asked you to send something."""
    msg = _call(client.send_message, channel_id.strip(), text)
    return tool_json({"sent": True, "message": msg}, pretty=pretty)


# --- Channels / categories ---------------------------------------------------


@mcp.tool()
def discord_create_channel(
    name: str,
    channel_type: str = "text",
    parent_id: str = "",
    topic: str = "",
    position: int = -1,
    pretty: bool = False,
) -> str:
    """Create a text/voice/announcement channel or a category. channel_type: text|voice|category|announcement."""
    kwargs: dict[str, Any] = {"channel_type": channel_type}
    if parent_id.strip():
        kwargs["parent_id"] = parent_id.strip()
    if topic:
        kwargs["topic"] = topic
    if position >= 0:
        kwargs["position"] = position
    created = _call(client.create_channel, name, **kwargs)
    return tool_json({"created": True, "channel": created}, pretty=pretty)


@mcp.tool()
def discord_edit_channel(
    channel_id: str,
    name: str = "",
    topic: str = "",
    parent_id: str = "",
    clear_parent: bool = False,
    position: int = -1,
    pretty: bool = False,
) -> str:
    """Rename, retopic, move, or reorder a channel/category. Use clear_parent=true to move out of a category."""
    kwargs: dict[str, Any] = {}
    if name.strip():
        kwargs["name"] = name.strip()
    if topic:
        kwargs["topic"] = topic
    if clear_parent:
        kwargs["parent_id"] = ""
    elif parent_id.strip():
        kwargs["parent_id"] = parent_id.strip()
    if position >= 0:
        kwargs["position"] = position
    updated = _call(client.edit_channel, channel_id.strip(), **kwargs)
    return tool_json({"edited": True, "channel": updated}, pretty=pretty)


@mcp.tool()
def discord_delete_channel(channel_id: str, pretty: bool = False) -> str:
    """Delete a channel or category by id. Irreversible."""
    result = _call(client.delete_channel, channel_id.strip())
    return tool_json(result, pretty=pretty)


@mcp.tool()
def discord_set_channel_permissions(
    channel_id: str,
    overwrite_id: str,
    allow: str = "0",
    deny: str = "0",
    overwrite_type: int = 0,
    pretty: bool = False,
) -> str:
    """Set a channel permission overwrite. overwrite_type: 0=role, 1=member. allow/deny are Discord permission bitfields as strings."""
    result = _call(
        client.edit_channel_permissions,
        channel_id.strip(),
        overwrite_id.strip(),
        allow=allow,
        deny=deny,
        overwrite_type=int(overwrite_type),
    )
    return tool_json(result, pretty=pretty)


# --- Roles -------------------------------------------------------------------


@mcp.tool()
def discord_list_roles(pretty: bool = False) -> str:
    """List roles of the configured Discord server."""
    roles = _call(client.list_roles)
    return tool_json({"roles": roles}, pretty=pretty)


@mcp.tool()
def discord_create_role(
    name: str,
    permissions: str = "",
    color: int = 0,
    hoist: bool = False,
    mentionable: bool = False,
    pretty: bool = False,
) -> str:
    """Create a role. permissions is the Discord permission bitfield string (optional)."""
    kwargs: dict[str, Any] = {}
    if permissions.strip():
        kwargs["permissions"] = permissions.strip()
    if color:
        kwargs["color"] = int(color)
    kwargs["hoist"] = bool(hoist)
    kwargs["mentionable"] = bool(mentionable)
    role = _call(client.create_role, name, **kwargs)
    return tool_json({"created": True, "role": role}, pretty=pretty)


@mcp.tool()
def discord_edit_role(
    role_id: str,
    name: str = "",
    permissions: str = "",
    color: int = -1,
    hoist: bool | None = None,
    mentionable: bool | None = None,
    pretty: bool = False,
) -> str:
    """Edit a role. Only pass fields you want to change."""
    kwargs: dict[str, Any] = {}
    if name.strip():
        kwargs["name"] = name.strip()
    if permissions.strip():
        kwargs["permissions"] = permissions.strip()
    if color >= 0:
        kwargs["color"] = int(color)
    if hoist is not None:
        kwargs["hoist"] = bool(hoist)
    if mentionable is not None:
        kwargs["mentionable"] = bool(mentionable)
    role = _call(client.edit_role, role_id.strip(), **kwargs)
    return tool_json({"edited": True, "role": role}, pretty=pretty)


@mcp.tool()
def discord_delete_role(role_id: str, pretty: bool = False) -> str:
    """Delete a role by id."""
    result = _call(client.delete_role, role_id.strip())
    return tool_json(result, pretty=pretty)


# --- Members / moderation ----------------------------------------------------


@mcp.tool()
def discord_list_members(limit: int = 100, after: str = "", pretty: bool = False) -> str:
    """List guild members (id, username, nick, roles). Requires Server Members Intent. Paginate with after=user_id."""
    members = _call(
        client.list_guild_members,
        limit=max(1, min(int(limit), 1000)),
        after=after.strip() or None,
    )
    return tool_json({"members": members}, pretty=pretty)


@mcp.tool()
def discord_set_member_roles(
    user_id: str,
    role_id: str,
    action: str = "add",
    pretty: bool = False,
) -> str:
    """Add or remove a role on a member. action: add|remove."""
    act = (action or "add").strip().lower()
    if act == "remove":
        result = _call(client.remove_role, user_id.strip(), role_id.strip())
    else:
        result = _call(client.add_role, user_id.strip(), role_id.strip())
    return tool_json(result, pretty=pretty)


@mcp.tool()
def discord_set_nickname(user_id: str, nick: str = "", pretty: bool = False) -> str:
    """Set a member's nickname. Pass empty nick to clear it."""
    result = _call(client.edit_member, user_id.strip(), nick=nick)
    return tool_json({"ok": True, "user_id": user_id.strip(), "nick": nick, "member": result}, pretty=pretty)


@mcp.tool()
def discord_timeout_member(user_id: str, minutes: int = 10, pretty: bool = False) -> str:
    """Timeout a member for N minutes (0 clears the timeout). Requires Moderate Members."""
    result = _call(client.timeout_member, user_id.strip(), minutes=int(minutes))
    return tool_json({"ok": True, "user_id": user_id.strip(), "minutes": int(minutes), "member": result}, pretty=pretty)


@mcp.tool()
def discord_kick_member(user_id: str, reason: str = "", pretty: bool = False) -> str:
    """Kick a member from the server. Requires Kick Members."""
    result = _call(client.kick_member, user_id.strip(), reason=reason.strip() or None)
    return tool_json(result, pretty=pretty)


@mcp.tool()
def discord_ban_member(
    user_id: str,
    delete_message_seconds: int = 0,
    reason: str = "",
    pretty: bool = False,
) -> str:
    """Ban a member. delete_message_seconds clears recent messages (0–604800). Requires Ban Members."""
    result = _call(
        client.ban_member,
        user_id.strip(),
        delete_message_seconds=int(delete_message_seconds),
        reason=reason.strip() or None,
    )
    return tool_json(result, pretty=pretty)


# --- Messages / invites ------------------------------------------------------


@mcp.tool()
def discord_edit_message(channel_id: str, message_id: str, text: str, pretty: bool = False) -> str:
    """Edit a message (usually one the bot sent)."""
    msg = _call(client.edit_message, channel_id.strip(), message_id.strip(), text)
    return tool_json({"edited": True, "message": msg}, pretty=pretty)


@mcp.tool()
def discord_delete_message(channel_id: str, message_id: str, pretty: bool = False) -> str:
    """Delete a message. Requires Manage Messages for others' messages."""
    result = _call(client.delete_message, channel_id.strip(), message_id.strip())
    return tool_json(result, pretty=pretty)


@mcp.tool()
def discord_create_invite(
    channel_id: str,
    max_age: int = 86400,
    max_uses: int = 0,
    temporary: bool = False,
    pretty: bool = False,
) -> str:
    """Create an invite link for a channel. max_age in seconds (0 = never); max_uses 0 = unlimited."""
    result = _call(
        client.create_invite,
        channel_id.strip(),
        max_age=int(max_age),
        max_uses=int(max_uses),
        temporary=bool(temporary),
    )
    return tool_json(result, pretty=pretty)
