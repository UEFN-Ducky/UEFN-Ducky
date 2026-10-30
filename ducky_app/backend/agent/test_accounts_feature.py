"""Several accounts and API keys, the way a person uses them.

Each test is one thing someone does or expects, end to end through the real code:
Settings buttons (PanelApi RPCs) → the key a chat uses (resolve_gateway_credential) →
a chat turn (make_provider) → the model picker's buttons (fetch_usage) → a coding
agent's login folder (runner._account_env). Only the key store and the two plugins
(an "openai" API gateway, a "codex" CLI) are stand-ins.

Run: cd ducky_app; py -3 -m pytest backend/agent/test_accounts_feature.py -v
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from backend.agent import accounts, providers
from backend.agent.coding_agents import runner
from backend.agent.model_fetch import fetch_usage, reset_usage_cache
from backend.agent.providers.base import StreamEvent, StreamEventKind
from backend.uefn_plugins import host
from frontend.ui_web.panel_api import PanelApi

QUOTA = "Error code: 429 - insufficient_quota"


class FakeOpenAI:
    """Answers "reply from <key>", or fails with a quota error for keys in ``out_of_quota``."""

    def __init__(self, key: str, world: SimpleNamespace) -> None:
        self.key, self.world = key, world

    async def stream_turn(self, **_kw):
        self.world.tried.append(self.key)
        if self.key in self.world.out_of_quota:
            raise RuntimeError(QUOTA)
        yield StreamEvent(kind=StreamEventKind.TEXT_DELTA, text=f"reply from {self.key}")
        yield StreamEvent(kind=StreamEventKind.DONE)


@pytest.fixture()
def ducky(tmp_path, monkeypatch):
    saved_keys: dict[str, str] = {"openai": "sk-default"}  # what Settings already had before this feature
    monkeypatch.setattr(accounts, "_data_root", lambda: tmp_path)
    monkeypatch.setattr(accounts._secrets, "get_key", saved_keys.get)
    monkeypatch.setattr(accounts._secrets, "set_key", lambda k, v: saved_keys.__setitem__(k, v))
    monkeypatch.setattr(accounts._secrets, "clear_key", lambda k: saved_keys.pop(k, None))
    accounts._CURRENT.clear()
    reset_usage_cache()

    world = SimpleNamespace(keys=saved_keys, tried=[], out_of_quota=set(), tmp=tmp_path)
    openai = {
        "factory": lambda key, model: FakeOpenAI(key, world),
        "fetch_usage": lambda key, **_kw: {"windows": [{"id": "rpm", "label": "Requests", "used": 1, "limit": 10}]},
    }
    codex_home = tmp_path / "home" / ".codex"
    (codex_home / "skills").mkdir(parents=True)
    (codex_home / "skills" / "verse.md").write_text("my skill")
    codex = {
        "account_env": lambda d: {"CODEX_HOME": str(d)},
        "account_shared": {"dirs": ["skills"], "files": [], "primary": lambda: codex_home},
    }
    monkeypatch.setattr(host, "get_llm_provider_registration", lambda n: openai if n == "openai" else None)
    monkeypatch.setattr(host, "get_coding_agent_registration", lambda n: codex if n == "codex" else None)
    monkeypatch.setattr(providers, "gateway_providers", lambda: ("openai",))
    monkeypatch.setattr("frontend.ui_web.provider_usage_log.log_gateway_usage", lambda **_k: None)
    world.codex_home = codex_home
    world.settings = PanelApi.__new__(PanelApi)  # the account RPCs keep no instance state
    return world


def add_key(ducky, label: str, key: str) -> str:
    out = ducky.settings.add_gateway_account("openai", label, "key")
    assert out["ok"], out
    assert ducky.settings.set_gateway_account_key("openai", out["id"], key) == {"ok": True}
    return out["id"]


def chat_turn() -> str:
    """One chat turn on OpenAI with the key Ducky would use now; returns the reply text."""

    async def go():
        provider = providers.make_provider("openai", host.resolve_gateway_credential("openai"), "gpt")
        return "".join([e.text async for e in provider.stream_turn(messages=[], system="") if e.text])

    return asyncio.run(go())


def picker_buttons() -> list[str]:
    switch = fetch_usage("openai", "", force=True).get("switch")
    return [o["label"] for o in switch["options"]] if switch else []


def picker_selected() -> str:
    return fetch_usage("openai", "", force=True)["switch"]["value"]


def test_1_existing_setup_keeps_working_with_one_account(ducky):
    (only,) = ducky.settings.list_gateway_accounts("openai")["accounts"]
    assert only["id"] == "default"
    assert host.resolve_gateway_credential("openai") == "sk-default"
    assert chat_turn() == "reply from sk-default"
    assert picker_buttons() == []  # one account: no switch under the bars
    assert runner._account_env("codex") == {}  # a CLI keeps its own login


def test_2_keys_added_in_settings_show_as_picker_buttons(ducky):
    add_key(ducky, "Work", "sk-work")
    add_key(ducky, "Team", "sk-team")
    buttons = picker_buttons()
    assert len(buttons) == 3 and buttons[1:] == ["Work", "Team"]
    assert picker_selected() == "default"


def test_3_picking_an_account_changes_the_key_chats_use(ducky):
    team = add_key(ducky, "Team", "sk-team")
    assert ducky.settings.set_gateway_account("openai", team) == {"ok": True}
    assert picker_selected() == team
    assert chat_turn() == "reply from sk-team"


def test_4_a_key_out_of_quota_finishes_the_same_turn_on_the_next_key(ducky):
    work = add_key(ducky, "Work", "sk-work")
    ducky.settings.set_gateway_account("openai", work)
    ducky.out_of_quota.add("sk-work")
    assert chat_turn() == "reply from sk-default"  # next account in the list, same turn
    assert ducky.tried == ["sk-work", "sk-default"]
    marked = {a["id"]: a["exhausted_until"] for a in ducky.settings.list_gateway_accounts("openai")["accounts"]}
    assert marked[work]  # Settings shows Work as full until it resets
    ducky.tried.clear()
    assert chat_turn() == "reply from sk-default"  # the next turn goes straight there
    assert ducky.tried == ["sk-default"]


def test_5_every_key_full_shows_the_real_error(ducky):
    add_key(ducky, "Work", "sk-work")
    ducky.out_of_quota.update({"sk-default", "sk-work"})
    with pytest.raises(RuntimeError, match="insufficient_quota"):
        chat_turn()


def test_6_switching_off_auto_switch_keeps_the_picked_key(ducky):
    add_key(ducky, "Work", "sk-work")
    assert ducky.settings.set_gateway_auto_switch("openai", False)["ok"]
    ducky.out_of_quota.add("sk-default")
    with pytest.raises(RuntimeError, match="insufficient_quota"):
        chat_turn()
    assert ducky.tried == ["sk-default"]


def test_7_a_coding_agent_login_runs_in_its_own_folder_with_your_skills(ducky):
    work = ducky.settings.add_gateway_account("codex", "Work", "login")["id"]
    ducky.settings.set_gateway_account("codex", work)
    env = runner._account_env("codex")
    folder = ducky.tmp / "coding_agents" / "accounts" / "codex" / work
    assert env == {"CODEX_HOME": str(folder)}
    assert (folder / "skills" / "verse.md").read_text() == "my skill"  # shared, not copied
    accounts.report_limit("codex")  # the CLI said this login hit its limit
    assert runner._account_env("codex") == {}  # next turn: back on the usual login


def test_8_removing_an_account_deletes_its_key_and_folder_but_not_shared_files(ducky):
    work = add_key(ducky, "Work", "sk-work")
    assert "openai@" + work in ducky.keys
    assert ducky.settings.remove_gateway_account("openai", work) == {"ok": True}
    assert "openai@" + work not in ducky.keys and ducky.keys["openai"] == "sk-default"
    assert picker_buttons() == []

    login = ducky.settings.add_gateway_account("codex", "Work", "login")["id"]
    ducky.settings.set_gateway_account("codex", login)
    runner._account_env("codex")  # links ~/.codex/skills in
    assert ducky.settings.remove_gateway_account("codex", login) == {"ok": True}
    assert not (ducky.tmp / "coding_agents" / "accounts" / "codex" / login).exists()
    assert (ducky.codex_home / "skills" / "verse.md").read_text() == "my skill"
    assert ducky.settings.remove_gateway_account("openai", "default")["ok"] is False  # the usual one stays
