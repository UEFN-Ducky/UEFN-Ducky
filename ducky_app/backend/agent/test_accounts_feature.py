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
import threading
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
    accounts._RUNNING.__dict__.clear()
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


def chat_turn(conv_id: str = "") -> str:
    """One chat turn on OpenAI with the key Ducky would use now; returns the reply text."""

    async def go():
        key = host.resolve_gateway_credential("openai", conv_id=conv_id)
        provider = providers.make_provider("openai", key, "gpt", conv_id=conv_id)
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


def test_9_two_chats_run_on_two_accounts_at_once(ducky):
    work = ducky.settings.add_gateway_account("codex", "Work", "login")["id"]
    team = ducky.settings.add_gateway_account("codex", "Team", "login")["id"]
    assert ducky.settings.set_chat_account("A", "codex", work) == {"ok": True}
    assert ducky.settings.set_chat_account("B", "codex", team) == {"ok": True}
    a_launched, b_launched, a_reported = threading.Event(), threading.Event(), threading.Event()
    seen: dict[str, object] = {}

    def chat_a():  # launches, then its CLI hits the 5-hour limit while B runs
        seen["env_a"] = runner._account_env("codex", "A")
        a_launched.set()
        b_launched.wait(5)
        accounts.report_limit("codex")
        a_reported.set()

    def chat_b():  # launched last, still running when A reports
        a_launched.wait(5)
        seen["env_b"] = runner._account_env("codex", "B")
        b_launched.set()
        a_reported.wait(5)
        seen["b_runs_as"] = accounts.current_account("codex")

    threads = [threading.Thread(target=chat_a), threading.Thread(target=chat_b)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    folder = ducky.tmp / "coding_agents" / "accounts" / "codex"
    assert seen["env_a"] == {"CODEX_HOME": str(folder / work)}
    assert seen["env_b"] == {"CODEX_HOME": str(folder / team)}
    assert accounts.exhausted_until("codex", work)  # A's limit marked Work only
    assert accounts.exhausted_until("codex", team) is None
    assert seen["b_runs_as"] == team
    assert runner._account_env("codex", "B") == {"CODEX_HOME": str(folder / team)}  # B unaffected
    assert runner._account_env("codex", "A") == {"CODEX_HOME": str(folder / team)}  # A moves on

    # API keys: each chat's turn uses its own key, a full key moves only that chat on.
    w, t = add_key(ducky, "Work", "sk-work"), add_key(ducky, "Team", "sk-team")
    ducky.settings.set_chat_account("A", "openai", w)
    ducky.settings.set_chat_account("B", "openai", t)
    replies: dict[str, str] = {}
    turns = [threading.Thread(target=lambda c=c: replies.__setitem__(c, chat_turn(c))) for c in "AB"]
    for th in turns:
        th.start()
    for th in turns:
        th.join(10)
    assert replies == {"A": "reply from sk-work", "B": "reply from sk-team"}
    ducky.out_of_quota.add("sk-work")
    assert chat_turn("A") == "reply from sk-team"  # next after Work, same turn
    assert accounts.exhausted_until("openai", w) and accounts.exhausted_until("openai", t) is None
    assert chat_turn("B") == "reply from sk-team"


def test_10_new_chats_use_the_default_and_the_picker_changes_only_this_chat(ducky):
    work = add_key(ducky, "Work", "sk-work")
    team = add_key(ducky, "Team", "sk-team")
    ducky.settings.set_gateway_account("openai", work)  # Settings: default for new chats
    assert ducky.settings.get_chat_account("new", "openai") == {
        "ok": True, "account": work, "label": "Work", "follows_default": True}
    assert chat_turn("new") == "reply from sk-work"

    assert ducky.settings.set_chat_account("mine", "openai", team) == {"ok": True}  # picker in one chat
    assert ducky.settings.get_chat_account("mine", "openai")["follows_default"] is False
    assert chat_turn("mine") == "reply from sk-team"
    assert chat_turn("other") == "reply from sk-work"  # other chats keep the default
    assert picker_selected() == work

    assert ducky.settings.set_chat_account("mine", "openai", "") == {"ok": True}  # follow default
    assert chat_turn("mine") == "reply from sk-work"
    assert ducky.settings.set_chat_account("mine", "openai", "nope")["ok"] is False

    ducky.settings.set_chat_account("mine", "openai", team)
    ducky.settings.remove_gateway_account("openai", team)  # removing clears chats on it
    assert ducky.settings.get_chat_account("mine", "openai")["follows_default"] is True
    assert "mine" not in accounts._load().get("chat_accounts", {})
