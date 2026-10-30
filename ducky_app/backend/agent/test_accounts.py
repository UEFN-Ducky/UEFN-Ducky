from __future__ import annotations

import os
import time

import pytest

from backend.agent import accounts


@pytest.fixture()
def store(tmp_path, monkeypatch):
    keys: dict[str, str] = {}
    monkeypatch.setattr(accounts, "_data_root", lambda: tmp_path)
    monkeypatch.setattr(accounts._secrets, "get_key", keys.get)
    monkeypatch.setattr(accounts._secrets, "set_key", lambda k, v: keys.__setitem__(k, v))
    monkeypatch.setattr(accounts._secrets, "clear_key", lambda k: keys.pop(k, None))
    return keys


def ids(provider="openai"):
    return [a["id"] for a in accounts.list_accounts(provider)]


def test_default_always_there(store):
    (only,) = accounts.list_accounts("openai")
    assert only["id"] == "default" and only["active"] and only["exhausted_until"] is None
    assert accounts.account_config_dir("openai", "default") is None
    with pytest.raises(ValueError):
        accounts.remove_account("openai", "default")


def test_add_rename_reorder_remove(store, tmp_path):
    a = accounts.add_account("openai", "Work", "key")
    b = accounts.add_account("openai", "", "key")
    assert ids() == ["default", a, b]
    assert accounts.list_accounts("openai")[2]["label"] == "Account 3"
    accounts.rename_account("openai", a, "Office")
    assert accounts.list_accounts("openai")[1]["label"] == "Office"
    accounts.reorder_accounts("openai", [b, "default"])
    assert ids() == [b, "default", a]

    accounts.set_account_key("openai", a, "sk-a")
    accounts.set_active_account("openai", a)
    folder = accounts.account_config_dir("openai", a)
    assert folder == tmp_path / "coding_agents" / "accounts" / "openai" / a and folder.is_dir()
    accounts.remove_account("openai", a)
    assert ids() == [b, "default"]
    assert accounts.active_account("openai") == "default"
    assert f"openai@{a}" not in store and not folder.exists()
    with pytest.raises(ValueError):
        accounts.add_account("openai", "x", "token")


def test_keys_stored_under_right_names(store):
    a = accounts.add_account("openai", "Work", "key")
    accounts.set_account_key("openai", "default", "sk-default")
    accounts.set_account_key("openai", a, "sk-work")
    assert store == {"openai": "sk-default", f"openai@{a}": "sk-work"}
    assert accounts.account_key("openai", a) == "sk-work"
    assert accounts.account_key("openai", "default") == "sk-default"


def test_pick_account(store):
    a = accounts.add_account("claude", "Two", "login")
    b = accounts.add_account("claude", "Three", "login")
    assert accounts.list_accounts("claude")[0]["kind"] == "login"
    accounts.set_active_account("claude", a)
    assert accounts.pick_account("claude") == a

    accounts.mark_exhausted("claude", a)
    assert accounts.exhausted_until("claude", a) > time.time() + 800
    assert accounts.pick_account("claude") == b  # next in order after the picked one
    assert accounts.pick_account("claude", is_full=lambda i: i == b) == "default"  # wraps

    accounts.set_auto_switch("claude", False)
    assert not accounts.auto_switch_enabled("claude")
    assert accounts.pick_account("claude") == a
    accounts.set_auto_switch("claude", True)

    now = time.time()
    accounts.mark_exhausted("claude", "default", now + 300)
    accounts.mark_exhausted("claude", b, now + 100)
    assert accounts.pick_account("claude") == b  # all full: earliest reset

    accounts.mark_exhausted("claude", b, now - 1)  # expired
    assert accounts.exhausted_until("claude", b) is None
    assert accounts.pick_account("claude") == b


def test_share_config_links_and_remove_keeps_target(store, tmp_path):
    primary = tmp_path / "primary"
    (primary / "skills").mkdir(parents=True)
    (primary / "skills" / "s.md").write_text("x")
    (primary / "settings.json").write_text("{}")
    a = accounts.add_account("claude", "Two", "login")
    folder = accounts.account_config_dir("claude", a)
    (folder / "own").mkdir()
    accounts.share_config(primary, folder, dirs=["skills", "missing"], files=["settings.json", "nope"])
    assert (folder / "skills" / "s.md").read_text() == "x"
    assert accounts._is_link(folder / "skills")
    assert (folder / "settings.json").read_text() == "{}"
    assert not os.path.lexists(folder / "missing")

    accounts.remove_account("claude", a)
    assert not folder.exists()
    assert (primary / "skills" / "s.md").read_text() == "x"  # never deleted through the link
