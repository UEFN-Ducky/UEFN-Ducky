"""Multi-bot Discord profiles (AppData JSON + namespaced secrets).

Profile fields live in ``discord_bots.json``. Tokens stay in the encrypted
secrets store as ``discord:<bot_id>`` (never in the JSON). The legacy single-bot
keys (``discord``, ``discord_guild``, ``discord_name``, ``discord_allowed_ids``)
migrate into a ``default`` profile on first load and keep working as fallbacks.
"""

from __future__ import annotations

import re
import threading
import uuid
from dataclasses import asdict, dataclass
from typing import Any

from frontend.atomic_json import read_json_object, write_json_atomic
from frontend.settings import default_app_data_dir

DEFAULT_BOT_ID = "default"
DEFAULT_PREFIX = "!ducky"
_LEGACY_TOKEN = "discord"
_LEGACY_GUILD = "discord_guild"
_LEGACY_NAME = "discord_name"
_LEGACY_ALLOWED = "discord_allowed_ids"
_LEGACY_CHANNEL = "discord_channel"

_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
_lock = threading.RLock()
_migrated = False


@dataclass
class BotProfile:
    id: str
    label: str = "Discord Bot"
    guild_id: str = ""
    post_as: str = ""
    allowed_ids: str = ""
    prefix: str = DEFAULT_PREFIX
    enabled: bool = True
    channel_id: str = ""  # last-watched channel for !command resume
    # When True, Discord member list shows the bot offline/invisible.
    # Default False: bot stays Online whenever it can respond (token + enabled).
    show_offline: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> BotProfile:
        bid = str(d.get("id") or "").strip() or DEFAULT_BOT_ID
        prefix = str(d.get("prefix") or DEFAULT_PREFIX).strip() or DEFAULT_PREFIX
        if not prefix.startswith("!"):
            prefix = "!" + prefix.lstrip("!")
        return cls(
            id=bid,
            label=str(d.get("label") or "Discord Bot").strip() or "Discord Bot",
            guild_id=str(d.get("guild_id") or "").strip(),
            post_as=str(d.get("post_as") or "").strip(),
            allowed_ids=str(d.get("allowed_ids") or "").strip(),
            prefix=prefix,
            enabled=bool(d.get("enabled", True)),
            channel_id=str(d.get("channel_id") or "").strip(),
            show_offline=bool(d.get("show_offline", False)),
        )


def bots_path():
    return default_app_data_dir() / "discord_bots.json"


def token_secret_key(bot_id: str) -> str:
    bid = _norm_id(bot_id)
    if bid == DEFAULT_BOT_ID:
        # Prefer namespaced; get_token falls back to legacy "discord".
        return f"discord:{DEFAULT_BOT_ID}"
    return f"discord:{bid}"


def _norm_id(bot_id: str | None) -> str:
    bid = (bot_id or DEFAULT_BOT_ID).strip() or DEFAULT_BOT_ID
    return bid if _ID_RE.match(bid) else DEFAULT_BOT_ID


def _read_raw() -> list[dict[str, Any]]:
    data = read_json_object(bots_path())
    bots = data.get("bots")
    return list(bots) if isinstance(bots, list) else []


def _write_raw(bots: list[dict[str, Any]]) -> None:
    write_json_atomic(bots_path(), {"bots": bots})


def _migrate_legacy_if_needed() -> None:
    """One-shot: legacy secrets → default profile when no bots file yet."""
    global _migrated
    if _migrated:
        return
    with _lock:
        if _migrated:
            return
        path = bots_path()
        if path.is_file() and _read_raw():
            _migrated = True
            return
        from backend.agent.secrets import get_key

        legacy_token = (get_key(_LEGACY_TOKEN) or "").strip()
        legacy_guild = (get_key(_LEGACY_GUILD) or "").strip()
        legacy_name = (get_key(_LEGACY_NAME) or "").strip()
        legacy_allowed = (get_key(_LEGACY_ALLOWED) or "").strip()
        legacy_channel = (get_key(_LEGACY_CHANNEL) or "").strip()
        if not legacy_token and not legacy_guild and not path.is_file():
            # Fresh install — leave empty; UI "Add bot" creates the first one.
            _migrated = True
            return
        if path.is_file() and _read_raw():
            _migrated = True
            return
        profile = BotProfile(
            id=DEFAULT_BOT_ID,
            label="Discord Bot",
            guild_id=legacy_guild,
            post_as=legacy_name,
            allowed_ids=legacy_allowed,
            prefix=DEFAULT_PREFIX,
            enabled=True,
            channel_id=legacy_channel,
        )
        _write_raw([profile.to_dict()])
        # Mirror token into namespaced key when only legacy exists.
        if legacy_token and not (get_key(token_secret_key(DEFAULT_BOT_ID)) or "").strip():
            from backend.agent.secrets import set_key

            set_key(token_secret_key(DEFAULT_BOT_ID), legacy_token)
        _migrated = True


def _profiles_from_raw(raw: list[dict[str, Any]]) -> list[BotProfile]:
    """Parse + dedupe by id (first wins). Guards raced double-creates."""
    seen: set[str] = set()
    out: list[BotProfile] = []
    for row in raw:
        if not isinstance(row, dict):
            continue
        profile = BotProfile.from_dict(row)
        if profile.id in seen:
            continue
        seen.add(profile.id)
        out.append(profile)
    return out


def list_bots() -> list[BotProfile]:
    _migrate_legacy_if_needed()
    with _lock:
        return _profiles_from_raw(_read_raw())


def get_bot(bot_id: str | None = None) -> BotProfile | None:
    bid = _norm_id(bot_id)
    for b in list_bots():
        if b.id == bid:
            return b
    if bid == DEFAULT_BOT_ID:
        # Soft default when nothing configured yet (keeps call sites simple).
        return BotProfile(id=DEFAULT_BOT_ID)
    return None


def require_bot(bot_id: str | None = None) -> BotProfile:
    b = get_bot(bot_id)
    if b is None:
        raise KeyError(f"Unknown Discord bot: {bot_id}")
    return b


def enabled_bots() -> list[BotProfile]:
    return [b for b in list_bots() if b.enabled]


def get_token(bot_id: str | None = None) -> str | None:
    from backend.agent.secrets import get_key

    bid = _norm_id(bot_id)
    tok = (get_key(token_secret_key(bid)) or "").strip()
    if tok:
        return tok
    if bid == DEFAULT_BOT_ID:
        legacy = (get_key(_LEGACY_TOKEN) or "").strip()
        return legacy or None
    return None


def get_guild_id(bot_id: str | None = None) -> str | None:
    from backend.agent.secrets import get_key

    bid = _norm_id(bot_id)
    b = get_bot(bid)
    if b and b.guild_id:
        return b.guild_id
    if bid == DEFAULT_BOT_ID:
        legacy = (get_key(_LEGACY_GUILD) or "").strip()
        return legacy or None
    return None


def get_post_as(bot_id: str | None = None) -> str:
    from backend.agent.secrets import get_key

    bid = _norm_id(bot_id)
    b = get_bot(bid)
    if b and b.post_as:
        return b.post_as
    if bid == DEFAULT_BOT_ID:
        return (get_key(_LEGACY_NAME) or "").strip()
    return ""


def get_allowed_ids(bot_id: str | None = None) -> str:
    from backend.agent.secrets import get_key

    bid = _norm_id(bot_id)
    b = get_bot(bid)
    if b and b.allowed_ids:
        return b.allowed_ids
    if bid == DEFAULT_BOT_ID:
        return (get_key(_LEGACY_ALLOWED) or "").strip()
    return ""


def get_prefix(bot_id: str | None = None) -> str:
    b = get_bot(bot_id)
    return (b.prefix if b else DEFAULT_PREFIX) or DEFAULT_PREFIX


def get_channel_id(bot_id: str | None = None) -> str:
    from backend.agent.secrets import get_key

    bid = _norm_id(bot_id)
    b = get_bot(bid)
    if b and b.channel_id:
        return b.channel_id
    if bid == DEFAULT_BOT_ID:
        return (get_key(_LEGACY_CHANNEL) or "").strip()
    return ""


def set_channel_id(bot_id: str | None, channel_id: str) -> None:
    bid = _norm_id(bot_id)
    cid = (channel_id or "").strip()
    with _lock:
        bots = list_bots()
        found = False
        out: list[dict[str, Any]] = []
        for b in bots:
            d = b.to_dict()
            if b.id == bid:
                d["channel_id"] = cid
                found = True
            out.append(d)
        if not found:
            # Create a minimal profile so the channel sticks.
            out.append(
                BotProfile(id=bid, channel_id=cid).to_dict()
            )
        _write_raw(out)
    if bid == DEFAULT_BOT_ID:
        from backend.agent.secrets import set_key

        set_key(_LEGACY_CHANNEL, cid)


def save_bot(
    *,
    bot_id: str | None = None,
    label: str | None = None,
    guild_id: str | None = None,
    post_as: str | None = None,
    allowed_ids: str | None = None,
    prefix: str | None = None,
    enabled: bool | None = None,
    show_offline: bool | None = None,
    token: str | None = None,
    create: bool = False,
) -> BotProfile:
    """Create or update a bot profile. ``token`` optional (blank = keep existing)."""
    from backend.agent.secrets import set_key

    with _lock:
        _migrate_legacy_if_needed()
        # Read under the same lock — never call list_bots() here (nested migrate).
        bots = _profiles_from_raw(_read_raw())
        if create or not bot_id:
            bid = _norm_id(bot_id) if bot_id else f"bot-{uuid.uuid4().hex[:8]}"
            if any(b.id == bid for b in bots):
                bid = f"bot-{uuid.uuid4().hex[:8]}"
            profile = BotProfile(
                id=bid,
                label=(label or "Discord Bot").strip() or "Discord Bot",
                guild_id=(guild_id or "").strip(),
                post_as=(post_as or "").strip(),
                allowed_ids=(allowed_ids or "").strip(),
                prefix=_normalize_prefix(prefix) if prefix is not None else DEFAULT_PREFIX,
                enabled=True if enabled is None else bool(enabled),
                show_offline=False if show_offline is None else bool(show_offline),
            )
            # First bot becomes "default" id when none exist yet.
            if not bots and bid != DEFAULT_BOT_ID and not bot_id:
                profile.id = DEFAULT_BOT_ID
            bots.append(profile)
        else:
            bid = _norm_id(bot_id)
            profile = None
            for i, b in enumerate(bots):
                if b.id == bid:
                    profile = b
                    if label is not None:
                        profile.label = label.strip() or profile.label
                    if guild_id is not None:
                        profile.guild_id = guild_id.strip()
                    if post_as is not None:
                        profile.post_as = post_as.strip()
                    if allowed_ids is not None:
                        profile.allowed_ids = allowed_ids.strip()
                    if prefix is not None:
                        profile.prefix = _normalize_prefix(prefix)
                    if enabled is not None:
                        profile.enabled = bool(enabled)
                    if show_offline is not None:
                        profile.show_offline = bool(show_offline)
                    bots[i] = profile
                    break
            if profile is None:
                raise KeyError(f"Unknown Discord bot: {bid}")

        _write_raw([b.to_dict() for b in bots])
        tok = (token or "").strip()
        if tok and tok != "••••••••":
            set_key(token_secret_key(profile.id), tok)
            if profile.id == DEFAULT_BOT_ID:
                set_key(_LEGACY_TOKEN, tok)
        # Keep legacy mirrors in sync for the default bot (old code paths / tools).
        if profile.id == DEFAULT_BOT_ID:
            set_key(_LEGACY_GUILD, profile.guild_id)
            set_key(_LEGACY_NAME, profile.post_as)
            set_key(_LEGACY_ALLOWED, profile.allowed_ids)
        return profile


def delete_bot(bot_id: str) -> None:
    from backend.agent.secrets import clear_key

    bid = _norm_id(bot_id)
    with _lock:
        bots = [b for b in list_bots() if b.id != bid]
        _write_raw([b.to_dict() for b in bots])
    clear_key(token_secret_key(bid))
    if bid == DEFAULT_BOT_ID:
        clear_key(_LEGACY_TOKEN)


def set_enabled(bot_id: str, enabled: bool) -> BotProfile:
    return save_bot(bot_id=bot_id, enabled=enabled)


def _normalize_prefix(prefix: str | None) -> str:
    p = (prefix or DEFAULT_PREFIX).strip() or DEFAULT_PREFIX
    if not p.startswith("!"):
        p = "!" + p.lstrip("!")
    return p.split()[0]  # no spaces in prefixes


def prune_empty_duplicate_bots() -> int:
    """Drop empty Add-bot clones (same label+prefix, no token/guild). Returns removed count."""
    removed = 0
    with _lock:
        bots = _profiles_from_raw(_read_raw())
        keep: list[BotProfile] = []
        seen_empty: set[tuple[str, str]] = set()
        for b in bots:
            empty = not get_token(b.id) and not (b.guild_id or "").strip()
            key = (b.label.strip().lower(), (b.prefix or DEFAULT_PREFIX).strip().lower())
            if empty and key in seen_empty:
                removed += 1
                continue
            if empty:
                seen_empty.add(key)
            keep.append(b)
        if removed:
            _write_raw([b.to_dict() for b in keep])
    return removed


def public_list() -> list[dict[str, Any]]:
    """UI-safe list (no tokens)."""
    from backend.agent.secrets import get_key

    prune_empty_duplicate_bots()
    out: list[dict[str, Any]] = []
    for b in list_bots():
        has_token = bool(get_token(b.id))
        out.append(
            {
                **b.to_dict(),
                "has_token": has_token,
                "configured": has_token
                and bool(
                    b.guild_id
                    or (get_key(_LEGACY_GUILD) if b.id == DEFAULT_BOT_ID else "")
                ),
            }
        )
    return out


if __name__ == "__main__":  # pragma: no cover
    assert _normalize_prefix("bob") == "!bob"
    assert _normalize_prefix("!ducky") == "!ducky"
    assert token_secret_key("default") == "discord:default"
    assert token_secret_key("bob") == "discord:bob"
    print("bots self-check ok")
