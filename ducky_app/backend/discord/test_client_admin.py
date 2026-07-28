"""Offline tests for Discord REST admin helpers (mocked _request)."""

from __future__ import annotations

import pytest

from backend.discord import client


@pytest.fixture(autouse=True)
def _stub_creds(monkeypatch):
    monkeypatch.setattr(client, "get_token", lambda bot_id=None: "test-token")
    monkeypatch.setattr(client, "get_guild_id", lambda bot_id=None: "guild-1")
    yield


def test_list_channels_text_only_filters(monkeypatch):
    raw = [
        {"id": "1", "name": "general", "type": 0, "position": 1},
        {"id": "2", "name": "Voice", "type": 2, "position": 2},
        {"id": "3", "name": "Category", "type": 4, "position": 0},
        {"id": "4", "name": "news", "type": 5, "position": 3},
    ]
    monkeypatch.setattr(client, "_request", lambda method, path, body=None, *, bot_id=None: raw)

    text = client.list_channels(text_only=True)
    assert {c["id"] for c in text} == {"1", "4"}
    assert all("type_name" in c for c in text)

    all_ch = client.list_channels(text_only=False)
    assert {c["id"] for c in all_ch} == {"1", "2", "3", "4"}
    assert any(c["type_name"] == "category" for c in all_ch)


def test_create_edit_delete_channel_paths(monkeypatch):
    calls: list[tuple] = []

    def fake(method, path, body=None, *, bot_id=None):
        calls.append((method, path, body))
        if method == "POST":
            return {"id": "99", "name": body["name"], "type": body["type"], "position": 0}
        if method == "PATCH":
            return {"id": "99", "name": body.get("name", "x"), "type": 0, "position": body.get("position", 0)}
        return None

    monkeypatch.setattr(client, "_request", fake)

    created = client.create_channel("lobby", channel_type="text", parent_id="cat-1")
    assert created["id"] == "99"
    assert calls[0][0] == "POST"
    assert calls[0][1] == "/guilds/guild-1/channels"
    assert calls[0][2]["type"] == 0
    assert calls[0][2]["parent_id"] == "cat-1"

    edited = client.edit_channel("99", name="lounge", position=2)
    assert edited["name"] == "lounge"
    assert calls[1] == ("PATCH", "/channels/99", {"name": "lounge", "position": 2})

    deleted = client.delete_channel("99")
    assert deleted["deleted"] is True
    assert calls[2] == ("DELETE", "/channels/99", None)


def test_edit_channel_permissions(monkeypatch):
    calls: list[tuple] = []

    def fake(method, path, body=None, *, bot_id=None):
        calls.append((method, path, body))
        return None

    monkeypatch.setattr(client, "_request", fake)
    out = client.edit_channel_permissions("ch1", "role1", allow="1024", deny="0", overwrite_type=0)
    assert out["ok"] is True
    assert calls[0][0] == "PUT"
    assert calls[0][1] == "/channels/ch1/permissions/role1"
    assert calls[0][2] == {"type": 0, "allow": "1024", "deny": "0"}

    client.delete_channel_permissions("ch1", "role1")
    assert calls[1] == ("DELETE", "/channels/ch1/permissions/role1", None)


def test_role_and_member_ops(monkeypatch):
    calls: list[tuple] = []

    def fake(method, path, body=None, *, bot_id=None):
        calls.append((method, path, body))
        if "roles" in path and method == "GET":
            return [{"id": "r1", "name": "Admin", "position": 5, "permissions": "8"}]
        if method == "POST" and path.endswith("/roles"):
            return {"id": "r2", "name": body["name"], "position": 1, "permissions": body.get("permissions", "0")}
        if method == "GET" and "/members" in path:
            return [{"user": {"id": "u1", "username": "ada"}, "roles": ["r1"], "nick": "Ada"}]
        return {"user": {"id": "u1", "username": "ada"}, "roles": ["r1"], "nick": "Ada"}

    monkeypatch.setattr(client, "_request", fake)

    roles = client.list_roles()
    assert roles[0]["name"] == "Admin"

    role = client.create_role("Mods", permissions="2048")
    assert role["id"] == "r2"
    assert any(c[0] == "POST" and c[1] == "/guilds/guild-1/roles" for c in calls)

    members = client.list_guild_members(limit=50)
    assert members[0]["id"] == "u1" and members[0]["nick"] == "Ada"

    client.add_role("u1", "r2")
    assert ("PUT", "/guilds/guild-1/members/u1/roles/r2", None) in calls

    client.kick_member("u1", reason="spam")
    assert any(c[0] == "DELETE" and c[1].startswith("/guilds/guild-1/members/u1") for c in calls)

    client.ban_member("u1", delete_message_seconds=60)
    assert ("PUT", "/guilds/guild-1/bans/u1", {"delete_message_seconds": 60}) in calls


def test_message_and_invite(monkeypatch):
    calls: list[tuple] = []

    def fake(method, path, body=None, *, bot_id=None):
        calls.append((method, path, body))
        if method == "PATCH":
            return {"id": "m1", "author": {"username": "bot"}, "content": body["content"]}
        if method == "POST" and path.endswith("/invites"):
            return {"code": "abc123"}
        return None

    monkeypatch.setattr(client, "_request", fake)

    msg = client.edit_message("ch1", "m1", "updated")
    assert msg["content"] == "updated"
    assert calls[0][1] == "/channels/ch1/messages/m1"

    client.delete_message("ch1", "m1")
    assert calls[1] == ("DELETE", "/channels/ch1/messages/m1", None)

    invite = client.create_invite("ch1", max_age=3600, max_uses=5)
    assert invite["url"] == "https://discord.gg/abc123"
    assert invite["code"] == "abc123"


def test_resolve_channel_type():
    assert client._resolve_channel_type("category") == 4  # noqa: SLF001
    assert client._resolve_channel_type(2) == 2
    with pytest.raises(client.DiscordError):
        client._resolve_channel_type("nope")  # noqa: SLF001
