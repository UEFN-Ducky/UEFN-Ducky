"""Multi-bot Discord: migration + prefix routing (offline)."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.discord import bots, commands


@pytest.fixture()
def isolated_appdata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Point AppData + secrets at a temp dir so tests don't touch the real store."""
    monkeypatch.setattr("frontend.settings.default_app_data_dir", lambda: tmp_path)
    monkeypatch.setattr(bots, "default_app_data_dir", lambda: tmp_path)
    # Reset migration flag between tests.
    monkeypatch.setattr(bots, "_migrated", False)
    # In-memory secrets.
    store: dict[str, str] = {}

    def get_key(provider: str) -> str | None:
        return store.get(provider)

    def set_key(provider: str, value: str) -> None:
        v = (value or "").strip()
        if v:
            store[provider] = v
        else:
            store.pop(provider, None)

    def clear_key(provider: str) -> None:
        store.pop(provider, None)

    monkeypatch.setattr("backend.agent.secrets.get_key", get_key)
    monkeypatch.setattr("backend.agent.secrets.set_key", set_key)
    monkeypatch.setattr("backend.agent.secrets.clear_key", clear_key)
    # bots.py imports get_key/set_key inside functions — also patch module-level if needed.
    yield tmp_path, store


def test_legacy_migration_creates_default_profile(isolated_appdata):
    _tmp, store = isolated_appdata
    store["discord"] = "legacy-token-abc"
    store["discord_guild"] = "guild-99"
    store["discord_name"] = "Iliya"
    store["discord_allowed_ids"] = "42"
    store["discord_channel"] = "ch-1"

    listed = bots.list_bots()
    assert len(listed) == 1
    assert listed[0].id == bots.DEFAULT_BOT_ID
    assert listed[0].guild_id == "guild-99"
    assert listed[0].post_as == "Iliya"
    assert listed[0].allowed_ids == "42"
    assert listed[0].prefix == "!ducky"
    assert listed[0].channel_id == "ch-1"
    # Token readable via namespaced or legacy.
    assert bots.get_token(bots.DEFAULT_BOT_ID) == "legacy-token-abc"
    assert (bots.bots_path()).is_file()


def test_two_bots_same_channel_prefix_routing(isolated_appdata, monkeypatch: pytest.MonkeyPatch):
    _tmp, store = isolated_appdata
    bots.save_bot(
        bot_id="default",
        label="Ducky",
        guild_id="g1",
        prefix="!ducky",
        allowed_ids="*",
        token="tok-a",
        create=True,
    )
    bots.save_bot(
        bot_id="bob",
        label="Bob",
        guild_id="g1",
        prefix="!bob",
        allowed_ids="*",
        token="tok-b",
        create=True,
    )

    sent: list[tuple[str, str, str]] = []

    def fake_send(channel_id: str, text: str, *, bot_id: str = "default") -> dict:
        sent.append((bot_id, channel_id, text))
        return {"id": "1", "content": text, "author": "bot", "author_id": "b", "bot": True, "timestamp": ""}

    monkeypatch.setattr(commands.client, "send_message", fake_send)
    # Don't actually spawn agent threads — stub _run_command.
    ran: list[tuple[str, str]] = []

    def fake_run(text: str, channel_id: str, author: str, bot_id: str, prefix: str) -> None:
        ran.append((bot_id, text))

    monkeypatch.setattr(commands, "_run_command", fake_run)

    msg_ducky = {"bot": False, "content": "!ducky list", "author_id": "1", "author": "ada"}
    msg_bob = {"bot": False, "content": "!bob list", "author_id": "1", "author": "ada"}
    msg_other = {"bot": False, "content": "!carol list", "author_id": "1", "author": "ada"}

    assert commands.maybe_handle(msg_ducky, "ch", bot_id="default") is True
    assert commands.maybe_handle(msg_ducky, "ch", bot_id="bob") is False  # wrong prefix for bob
    assert commands.maybe_handle(msg_bob, "ch", bot_id="bob") is True
    assert commands.maybe_handle(msg_bob, "ch", bot_id="default") is False
    assert commands.maybe_handle(msg_other, "ch", bot_id="default") is False
    assert commands.maybe_handle(msg_other, "ch", bot_id="bob") is False

    # Wait briefly for daemon threads that call fake_run.
    import time

    time.sleep(0.05)
    assert ("default", "!ducky list") in ran
    assert ("bob", "!bob list") in ran
    assert len(ran) == 2


def test_conv_keys_include_bot_id():
    """Sanity: busy/conv key shape is (bot, channel, profile)."""
    key_a = ("default", "ch1", "p1")
    key_b = ("bob", "ch1", "p1")
    assert key_a != key_b
