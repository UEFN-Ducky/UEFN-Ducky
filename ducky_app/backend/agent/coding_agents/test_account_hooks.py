from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.agent import accounts
from backend.agent.coding_agents import runner
from backend.uefn_plugins import host
from frontend.ui_web.panel_api import PanelApi


@pytest.fixture()
def reg(tmp_path, monkeypatch):
    keys: dict[str, str] = {}
    monkeypatch.setattr(accounts, "_data_root", lambda: tmp_path)
    monkeypatch.setattr(accounts._secrets, "get_key", keys.get)
    monkeypatch.setattr(accounts._secrets, "set_key", lambda k, v: keys.__setitem__(k, v))
    monkeypatch.setattr(accounts._secrets, "clear_key", lambda k: keys.pop(k, None))
    accounts._CURRENT.clear()
    accounts._RUNNING.__dict__.clear()
    accounts._STATUS.clear()
    from backend.agent.model_fetch import reset_usage_cache

    reset_usage_cache()
    primary = tmp_path / "codex-home"
    (primary / "skills").mkdir(parents=True)
    calls: list = []
    row = {
        "account_usage": lambda d: calls.append(("usage", d)) or {
            "windows": [{"id": "5h", "label": "5-hour limit", "used": 21, "limit": 100}]},
        "account_env": lambda d: {"CODEX_HOME": str(d)},
        "account_login": lambda d: {"argv": ["codex", "login"], "env": {"CODEX_HOME": str(d)}},
        "account_status": lambda d: {"loggedIn": d is not None, "email": "w@x"},
        "account_logout": lambda d: calls.append(("logout", d)),
        "account_shared": {"dirs": ["skills"], "files": [], "primary": lambda: primary},
    }
    monkeypatch.setattr(host, "get_coding_agent_registration", lambda aid: row if aid == "codex" else None)
    return SimpleNamespace(keys=keys, calls=calls, primary=primary, tmp=tmp_path)


def test_env_empty_for_default_and_set_for_picked_account(reg):
    assert runner._account_env("codex") == {}
    work = accounts.add_account("codex", "Work", "login")
    accounts.set_active_account("codex", work)
    env = runner._account_env("codex")
    folder = reg.tmp / "coding_agents" / "accounts" / "codex" / work
    assert env == {"CODEX_HOME": str(folder)}
    assert accounts._is_link(folder / "skills")  # account_shared linked in
    assert accounts.current_account("codex") == work

    accounts.report_limit("codex")  # adapters: the running account is full
    assert accounts.exhausted_until("codex", work)
    assert runner._account_env("codex") == {}  # auto-switch back to default
    assert accounts.current_account("codex") == "default"


def test_env_never_raises(reg, monkeypatch):
    monkeypatch.setattr(accounts, "pick_account", lambda *_a, **_k: 1 / 0)
    assert runner._account_env("codex") == {}


def test_rpc_round_trip(reg, monkeypatch):
    api = PanelApi.__new__(PanelApi)  # the RPCs touch no instance state
    out = api.add_gateway_account("Codex", "Work", "login")
    assert out["ok"]
    work = out["id"]
    assert api.rename_gateway_account("codex", work, "Office") == {"ok": True}
    assert api.set_gateway_account("codex", work) == {"ok": True}
    assert api.set_gateway_account_key("codex", work, "sk-1") == {"ok": True}
    assert reg.keys == {f"codex@{work}": "sk-1"}
    assert api.reorder_gateway_accounts("codex", [work])["ok"]
    assert api.set_gateway_auto_switch("codex", False)["ok"]
    listed = api.list_gateway_accounts("codex")
    assert [a["id"] for a in listed["accounts"]] == [work, "default"]
    assert listed["accounts"][0]["label"] == "Office" and listed["accounts"][0]["active"]
    assert listed["auto_switch"] is False

    assert api.gateway_account_status("codex", work) == {"ok": True, "status": {"loggedIn": True, "email": "w@x"}}
    assert api.logout_gateway_account("codex", work)["ok"]
    assert reg.calls == [("logout", reg.tmp / "coding_agents" / "accounts" / "codex" / work)]

    spawned: dict = {}

    class Mgr:
        def spawn(self, **kw):
            spawned.update(kw)
            return {"ok": True, "session_id": "t1"}

    import frontend.ui_web.terminal as terminal

    monkeypatch.setattr(terminal, "get_terminal_manager", lambda: Mgr())
    monkeypatch.setattr("frontend.ui_web.panel_api.PanelSettings.load", lambda: SimpleNamespace(uefn_project_root=""))
    assert api.login_gateway_account("codex", work) == {"ok": True, "session_id": "t1"}
    assert spawned["command"] == ["codex", "login"] and spawned["env_extra"]["CODEX_HOME"].endswith(work)

    assert api.remove_gateway_account("codex", "default")["ok"] is False
    assert api.set_gateway_account("codex", "nope")["ok"] is False
    assert api.gateway_account_status("openai", "default")["ok"] is False  # no hook
    assert api.list_gateway_accounts("")["ok"] is False
    assert api.remove_gateway_account("codex", work) == {"ok": True}
    assert reg.keys == {}


def test_usage_per_account_calls_the_right_hook(reg, monkeypatch):
    api = PanelApi.__new__(PanelApi)
    work = api.add_gateway_account("codex", "Work", "login")["id"]
    out = api.gateway_account_usage("codex", work)
    assert out == {"ok": True, "windows": [{"id": "5h", "label": "5-hour limit", "used": 21.0, "limit": 100.0}]}
    folder = reg.tmp / "coding_agents" / "accounts" / "codex" / work
    assert reg.calls == [("usage", folder)]
    api.gateway_account_usage("codex", work)  # cached
    api.gateway_account_usage("codex", "default")
    assert reg.calls == [("usage", folder), ("usage", None)]  # default = the CLI's own folder
    assert api.gateway_account_usage("codex", "nope")["ok"] is False

    # An API gateway: the plugin's fetch_usage gets that account's key.
    seen: list = []
    gw = {"fetch_usage": lambda key, **kw: seen.append((key, kw.get("account"))) or {
        "windows": [{"id": "rpm", "label": "Requests", "used": 1, "limit": 10}]}}
    monkeypatch.setattr(host, "get_llm_provider_registration", lambda n: gw if n == "openai" else None)
    team = api.add_gateway_account("openai", "Team", "key")["id"]
    api.set_gateway_account_key("openai", team, "sk-team")
    assert api.gateway_account_usage("openai", team)["windows"][0]["label"] == "Requests"
    assert seen == [("sk-team", team)]
    gw["fetch_usage"] = lambda key, **kw: 1 / 0
    assert api.gateway_account_usage("openai", "default")["windows"] == []  # plugin error: no bars
    monkeypatch.setattr("backend.agent.model_fetch.fetch_account_usage", lambda *a, **k: 1 / 0)
    assert api.gateway_account_usage("openai", team)["ok"] is False  # never raises


def test_list_shows_login_status_cached_and_keys(reg, monkeypatch):
    api = PanelApi.__new__(PanelApi)
    work = api.add_gateway_account("codex", "Work", "login")["id"]
    asked: list = []
    real = accounts.account_status
    monkeypatch.setattr(accounts, "account_status", lambda a, i: asked.append(i) or real(a, i))
    listed = api.list_gateway_accounts("codex")
    assert listed["default"] == "default"
    status = {a["id"]: a["status"] for a in listed["accounts"]}
    assert status == {"default": {"loggedIn": False, "email": "w@x"}, work: {"loggedIn": True, "email": "w@x"}}
    api.list_gateway_accounts("codex")
    assert sorted(asked) == sorted(["default", work])  # cached: asked once each

    import threading

    gate = threading.Event()
    accounts._STATUS.clear()
    monkeypatch.setattr(accounts, "account_status", lambda a, i: gate.wait(5) and {"loggedIn": True})
    import time

    t0 = time.monotonic()
    slow = api.list_gateway_accounts("codex")
    assert time.monotonic() - t0 < 3 and all(a["status"] is None for a in slow["accounts"])
    gate.set()

    team = api.add_gateway_account("openai", "Team", "key")["id"]
    api.set_gateway_account("openai", team)
    api.set_gateway_account_key("openai", team, "sk-team")
    keys = api.list_gateway_accounts("openai")
    assert {a["id"]: a["has_key"] for a in keys["accounts"]} == {"default": False, team: True}
    assert keys["default"] == team
