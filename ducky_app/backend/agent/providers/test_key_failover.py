from __future__ import annotations

import asyncio

import pytest

from backend.agent import accounts
from backend.agent import providers
from backend.agent.providers.base import StreamEvent, StreamEventKind as K


class Fake:
    """Key ``"sk-a"`` / ``"sk-b"`` behaviour comes from ``plan[key]``."""

    def __init__(self, key: str, plan: dict, calls: list) -> None:
        self.key, self.plan, self.calls = key, plan, calls

    async def stream_turn(self, **_kw):
        self.calls.append(self.key)
        for step in self.plan.get(self.key, ["ok"]):
            if step == "raise":
                raise RuntimeError("Error code: 429 - insufficient_quota")
            if step == "err":
                yield StreamEvent(kind=K.ERROR, error="rate_limit_exceeded")
                return
            if step == "text":
                yield StreamEvent(kind=K.TEXT_DELTA, text="hi")
            if step == "ok":
                yield StreamEvent(kind=K.TEXT_DELTA, text=f"from {self.key}")
                yield StreamEvent(kind=K.DONE)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    keys: dict[str, str] = {}
    monkeypatch.setattr(accounts, "_data_root", lambda: tmp_path)
    monkeypatch.setattr(accounts._secrets, "get_key", keys.get)
    monkeypatch.setattr(accounts._secrets, "set_key", lambda k, v: keys.__setitem__(k, v))
    plan: dict = {}
    calls: list = []
    reg = {"factory": lambda key, model: Fake(key, plan, calls)}
    monkeypatch.setattr("backend.uefn_plugins.host.get_llm_provider_registration", lambda _n: reg)
    monkeypatch.setattr(providers, "gateway_providers", lambda: ("kimi",))
    monkeypatch.setattr("frontend.ui_web.provider_usage_log.log_gateway_usage", lambda **_k: None)
    keys["kimi"] = "sk-a"
    b = accounts.add_account("kimi", "B", "key")
    accounts.set_account_key("kimi", b, "sk-b")
    return plan, calls, b, reg


def run(key: str = "sk-a"):
    async def go():
        p = providers.make_provider("kimi", key, "m")
        return [e async for e in p.stream_turn(messages=[], system="")]

    return asyncio.run(go())


def texts(events):
    return [e.text for e in events if e.kind == K.TEXT_DELTA]


@pytest.mark.parametrize("step", ["raise", "err"])
def test_quota_before_output_retries_on_next_key(env, step):
    plan, calls, b, _ = env
    plan["sk-a"] = [step]
    events = run()
    assert calls == ["sk-a", "sk-b"]
    assert texts(events) == ["from sk-b"] and events[-1].kind == K.DONE
    assert accounts.exhausted_until("kimi", "default")
    assert accounts.pick_account("kimi") == b


def test_all_keys_full_surfaces_the_error(env):
    plan, calls, _, _ = env
    plan["sk-a"] = plan["sk-b"] = ["err"]
    events = run()
    assert calls == ["sk-a", "sk-b"]
    assert [e.kind for e in events] == [K.ERROR]
    plan["sk-a"] = plan["sk-b"] = ["raise"]
    with pytest.raises(RuntimeError):
        run()


def test_error_after_text_no_retry_but_next_turn_switches(env):
    plan, calls, b, _ = env
    plan["sk-a"] = ["text", "raise"]
    with pytest.raises(RuntimeError):
        run()
    assert calls == ["sk-a"]
    assert accounts.pick_account("kimi") == b


def test_auto_switch_off_no_retry(env):
    plan, calls, _, _ = env
    accounts.set_auto_switch("kimi", False)
    plan["sk-a"] = ["err"]
    assert [e.kind for e in run()] == [K.ERROR]
    assert calls == ["sk-a"]


def test_typed_key_and_plugin_quota_check(env):
    plan, calls, _, reg = env
    plan["sk-x"] = ["err"]
    assert [e.kind for e in run("sk-x")] == [K.ERROR]  # not a saved account: no failover
    calls.clear()
    reg["is_quota_error"] = lambda text: False
    plan["sk-a"] = ["err"]
    assert [e.kind for e in run()] == [K.ERROR] and calls == ["sk-a"]
