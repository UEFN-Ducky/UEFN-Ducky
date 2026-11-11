"""A gateway's picture maker is set up when its key is a key account, not only a plain saved key."""

from __future__ import annotations


def test_a_key_account_counts_as_the_gateways_key(monkeypatch) -> None:
    """Nov 11 2026: Google had two key accounts and no plain key; ducky_generate_image said
    "Image backend google is unavailable" while the plugin itself made pictures."""
    from backend.agent import accounts, secrets
    from backend.automations import media

    monkeypatch.setattr(secrets, "get_key", lambda _p: "")
    monkeypatch.setattr(accounts, "pick_account", lambda p, **_k: "ed86")
    monkeypatch.setattr(accounts, "account_key", lambda p, a: "KEY" if (p, a) == ("gemini", "ed86") else "")
    assert media._has_key("gemini") is True
    assert media._has_key("kimi") is False

    monkeypatch.setattr(secrets, "get_key", lambda p: "PLAIN" if p == "kimi" else "")
    assert media._has_key("kimi") is True  # the plain key still counts

    def boom(*_a, **_k):
        raise KeyError("no such provider")

    monkeypatch.setattr(accounts, "pick_account", boom)
    assert media._has_key("gemini") is False  # never raises
