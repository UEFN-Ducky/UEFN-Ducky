"""Per-bot background pollers for the active Discord channel.

Each enabled bot gets its own daemon thread + channel cursor. Opening a channel
on bot A re-points only that bot's poller. Events include ``bot_id`` so the UI
can filter by the selected bot.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any

from backend.discord import bots, client

_POLL_INTERVAL_S = 4.0
_ERROR_BACKOFF_S = 15.0

_lock = threading.Lock()
_states: dict[str, "_BotPoller"] = {}


@dataclass
class _BotPoller:
    bot_id: str
    wake: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None
    active_channel: str | None = None
    last_id: str | None = None
    last_seen: dict[str, Any] | None = None
    last_poll_ts: float = 0.0
    stop_flag: bool = False


def _push(message: dict[str, Any], channel_id: str, bot_id: str) -> None:
    from frontend.ui_web.verse_editor.panel_events import push_agent_event

    push_agent_event(
        {
            "type": "discord_message",
            "channel_id": channel_id,
            "bot_id": bot_id,
            "discord": message,
        }
    )


def _get_or_create(bot_id: str) -> _BotPoller:
    bid = (bot_id or bots.DEFAULT_BOT_ID).strip() or bots.DEFAULT_BOT_ID
    with _lock:
        st = _states.get(bid)
        if st is None:
            st = _BotPoller(bot_id=bid)
            _states[bid] = st
        return st


def _run(st: _BotPoller) -> None:
    while not st.stop_flag:
        with _lock:
            channel = st.active_channel
            after = st.last_id
            bid = st.bot_id
        if not channel:
            st.wake.wait(timeout=30.0)
            st.wake.clear()
            continue
        try:
            new = client.fetch_messages(channel, after=after, bot_id=bid)
            st.last_poll_ts = time.time()
        except client.DiscordError:
            st.wake.wait(timeout=_ERROR_BACKOFF_S)
            st.wake.clear()
            continue
        if new:
            last = new[-1]
            st.last_seen = {
                "channel_id": channel,
                "author": last.get("author", ""),
                "content_len": len(last.get("content", "")),
                "preview": (last.get("content", "") or "")[:60],
                "ts": last.get("timestamp", ""),
            }
            with _lock:
                # Only advance/emit while the same channel is still open.
                if st.active_channel == channel:
                    st.last_id = last["id"]
                    emit = True
                else:
                    emit = False
            if emit:
                from backend.discord import commands

                for m in new:
                    _push(m, channel, bid)
                    try:
                        commands.maybe_handle(m, channel, bot_id=bid)
                    except Exception:
                        pass
        st.wake.wait(timeout=_POLL_INTERVAL_S)
        st.wake.clear()


def debug_state(bot_id: str | None = None) -> dict[str, Any]:
    """Snapshot for the Settings 'Diagnose' button."""
    bid = (bot_id or bots.DEFAULT_BOT_ID).strip() or bots.DEFAULT_BOT_ID
    st = _get_or_create(bid)
    with _lock:
        watching = st.active_channel
        alive = st.thread is not None and st.thread.is_alive()
        last_seen = st.last_seen
        last_poll_ts = st.last_poll_ts
    return {
        "bot_id": bid,
        "watching_channel_id": watching or "",
        "poller_alive": alive,
        "last_poll_age_s": round(time.time() - last_poll_ts, 1) if last_poll_ts else None,
        "last_seen": last_seen,
    }


def _ensure_thread(st: _BotPoller) -> None:
    if st.thread is not None and st.thread.is_alive() and not st.stop_flag:
        return
    st.stop_flag = False
    st.thread = threading.Thread(
        target=_run, args=(st,), name=f"discord-poller-{st.bot_id}", daemon=True
    )
    st.thread.start()


def open_channel(channel_id: str, newest_id: str | None, *, bot_id: str | None = None) -> None:
    """Point this bot's poller at ``channel_id``; only messages after ``newest_id`` emit."""
    st = _get_or_create(bot_id)
    with _lock:
        st.active_channel = (channel_id or "").strip() or None
        st.last_id = newest_id
    _ensure_thread(st)
    st.wake.set()


def stop(bot_id: str | None = None) -> None:
    """Detach this bot from any channel (poller thread parks itself)."""
    st = _get_or_create(bot_id)
    with _lock:
        st.active_channel = None
    st.wake.set()


def stop_bot(bot_id: str) -> None:
    """Fully stop a bot's poller (used when disabling/deleting a bot)."""
    bid = (bot_id or "").strip()
    with _lock:
        st = _states.get(bid)
        if st is None:
            return
        st.active_channel = None
        st.stop_flag = True
    st.wake.set()


def ensure_watching(channel_id: str, *, bot_id: str | None = None) -> None:
    """Start watching ``channel_id`` if this bot isn't polling yet.

    App start / panel mount call this with the last-used channel so commands
    work without the panel ever being opened. Skips emitting history.
    """
    cid = (channel_id or "").strip()
    if not cid:
        return
    st = _get_or_create(bot_id)
    with _lock:
        if st.active_channel:
            return
        bid = st.bot_id

    def _boot() -> None:
        try:
            newest = client.fetch_messages(cid, limit=1, bot_id=bid)
        except client.DiscordError:
            return
        with _lock:
            if st.active_channel:  # a real open won the race — keep it
                return
        open_channel(cid, newest[-1]["id"] if newest else None, bot_id=bid)

    threading.Thread(target=_boot, daemon=True, name=f"discord-poll-boot-{bid}").start()


def sync_enabled_bots() -> None:
    """Ensure pollers exist for enabled bots; stop disabled ones."""
    enabled_ids = {b.id for b in bots.enabled_bots()}
    with _lock:
        known = set(_states.keys())
    for bid in known - enabled_ids:
        stop_bot(bid)
    for b in bots.enabled_bots():
        if not client.get_token(b.id):
            continue
        cid = bots.get_channel_id(b.id)
        if cid:
            ensure_watching(cid, bot_id=b.id)
        else:
            _ensure_thread(_get_or_create(b.id))


if __name__ == "__main__":  # pragma: no cover
    st = _BotPoller(bot_id="default")
    assert st.active_channel is None
    print("poller self-check ok")
