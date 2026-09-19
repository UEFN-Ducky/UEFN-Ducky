"""Old chats recapture rules / tools / bootstrap after an EXE bump."""

from __future__ import annotations

from types import SimpleNamespace


def test_cache_bust_needed_when_stamp_version_differs():
    from frontend.ship_newest import conversation_cache_bust_needed

    assert conversation_cache_bust_needed(None, "1.2.3")
    assert conversation_cache_bust_needed({"version": "1.0.0"}, "1.2.3")
    assert conversation_cache_bust_needed({}, "1.2.3")
    assert not conversation_cache_bust_needed({"version": "1.2.3"}, "1.2.3")


def test_maybe_bust_wipes_on_stale_stamp(monkeypatch):
    from frontend import ship_newest

    calls: list[int] = []
    monkeypatch.setattr(ship_newest, "_read_ship_stamp", lambda: {"version": "0.1.0"})
    monkeypatch.setattr(ship_newest, "__version__", "9.9.9", raising=False)
    monkeypatch.setattr("frontend.__version__", "9.9.9")

    def _wipe() -> None:
        calls.append(1)

    monkeypatch.setattr(
        "frontend.ui_web.project_chats.invalidate_all_projects_conversation_caches",
        _wipe,
    )
    assert ship_newest.maybe_bust_conversation_caches_for_app_version() is True
    assert calls == [1]


def test_maybe_bust_skips_same_version(monkeypatch):
    from frontend import ship_newest

    calls: list[int] = []
    monkeypatch.setattr(ship_newest, "_read_ship_stamp", lambda: {"version": "9.9.9"})
    monkeypatch.setattr("frontend.__version__", "9.9.9")
    monkeypatch.setattr(
        "frontend.ui_web.project_chats.invalidate_all_projects_conversation_caches",
        lambda: calls.append(1),
    )
    assert ship_newest.maybe_bust_conversation_caches_for_app_version() is False
    assert calls == []


def test_drift_includes_live_rules_and_tool_index():
    from backend.agent.prompt_cache import _drift_lines

    live = {
        "rules": "NEW RULES BODY",
        "tool_index": "NEW TOOL INDEX",
        "skill": "same skill",
        "mcp": "same mcp",
    }
    frozen = {
        "rules": "old rules",
        "tool_index": "old tools",
        "skill": "same skill",
        "mcp": "same mcp",
    }
    lines = _drift_lines(live, frozen)
    text = "\n".join(lines)
    assert "### Updated rules" in text
    assert "NEW RULES BODY" in text
    assert "### Updated tool index" in text
    assert "NEW TOOL INDEX" in text
    assert "Updated skill" not in text
    assert "Updated MCP" not in text


def test_inject_bootstrap_on_stale_resume():
    from backend.agent.coding_agents.runner import inject_bootstrap_if_stale

    conv = SimpleNamespace(rules_app_version="")
    out = inject_bootstrap_if_stale(
        conv,
        "user said hi",
        "FULL BOOTSTRAP",
        app_version="2.0.0",
        session_id="sess-1",
    )
    assert "FULL BOOTSTRAP" in out
    assert "user said hi" in out
    assert conv.rules_app_version == "2.0.0"
    again = inject_bootstrap_if_stale(
        conv,
        "next",
        "FULL BOOTSTRAP",
        app_version="2.0.0",
        session_id="sess-1",
    )
    assert again == "next"


def test_inject_bootstrap_stamps_new_session_without_dup():
    from backend.agent.coding_agents.runner import inject_bootstrap_if_stale

    conv = SimpleNamespace(rules_app_version="")
    out = inject_bootstrap_if_stale(
        conv,
        "user said hi",
        "FULL BOOTSTRAP",
        app_version="2.0.0",
        session_id="",
    )
    assert out == "user said hi"
    assert conv.rules_app_version == "2.0.0"
