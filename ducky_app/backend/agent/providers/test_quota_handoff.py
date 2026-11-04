from types import SimpleNamespace

from backend.agent import providers
from frontend.settings import PanelSettings
from frontend.ui_web.agent_turns import errors as am
from frontend.ui_web.agent_turns.testing import patch_turns

LIMIT = "You've used your Ducky AI for now. It's back in 4 h."


def _env(monkeypatch, *, available: bool):
    conv = SimpleNamespace(id="c1", coding_agent="ducky", model="ducky-ai", provider="ducky_ai")
    runs, pushed = [], []
    adapter = SimpleNamespace(detect=lambda _s: SimpleNamespace(enabled=True, available=available))
    monkeypatch.setattr("backend.agent.coding_agents.base.get_adapter", lambda aid: adapter if aid == "claude_code" else None)
    monkeypatch.setattr(PanelSettings, "load", staticmethod(lambda: None))
    patch_turns(monkeypatch, "load_conversation", lambda _cid: conv)
    patch_turns(monkeypatch, "save_conversation", lambda _c: None)
    patch_turns(monkeypatch, "run_message", lambda *a, **k: runs.append((a, k)))
    return conv, runs, pushed


def test_plan_limit_text_detected_and_not_retried():
    assert providers.is_plan_limit(LIMIT)
    assert not providers.is_plan_limit("Ducky AI is busy right now")
    assert not providers._TRANSIENT_RE.search(LIMIT)


def test_quota_routes_turn_to_claude_code_opus(monkeypatch):
    conv, runs, pushed = _env(monkeypatch, available=True)
    assert am._continue_on_claude_code("c1", "agent", pushed.append) is True
    assert (conv.coding_agent, conv.model, conv.provider) == ("claude_code", "claude-opus-5-5", "")
    (args, kw), = runs
    assert args == ("c1", "", "agent", "claude-opus-5-5") and kw["resume"] is True
    assert pushed[-1]["text"] == "Ducky AI limit reached, continuing on Claude Code Opus 5.5"


def test_no_claude_code_stops_with_message(monkeypatch):
    conv, runs, pushed = _env(monkeypatch, available=False)
    assert am._continue_on_claude_code("c1", "agent", pushed.append) is False
    assert runs == [] and conv.coding_agent == "ducky"
    assert pushed[-1]["type"] == "error" and "Claude Code is not installed or signed in" in pushed[-1]["text"]
