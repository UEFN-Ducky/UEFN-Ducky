"""ducky_get_tools(pattern=) ranks through the search index, then the old all-words matches."""

from __future__ import annotations

from types import SimpleNamespace

from backend.tools.panel import ducky_panel


def _tools(*names: str) -> list:
    return [SimpleNamespace(name=n, description=f"{n.replace('_', ' ')} tool", inputSchema={"type": "object"})
            for n in names]


def test_index_hits_lead_then_word_matches(monkeypatch) -> None:
    from backend.search import query

    tools = _tools("save_current_level", "save_all_dirty", "photoshop_save_document")
    by_name = {t.name: t for t in tools}
    monkeypatch.setattr(query, "search_tools", lambda text, allowed, limit=20: [{"tool": "save_current_level"}])
    names = ducky_panel._ranked_tool_names(tools, by_name, "save", 10)
    assert names[0] == "save_current_level"
    assert set(names) == {"save_current_level", "save_all_dirty", "photoshop_save_document"}


def test_index_failure_keeps_the_old_search(monkeypatch) -> None:
    from backend.search import query

    def boom(*a, **k):
        raise RuntimeError("index unavailable")

    tools = _tools("save_current_level", "spawn_actor")
    monkeypatch.setattr(query, "search_tools", boom)
    assert ducky_panel._ranked_tool_names(tools, {t.name: t for t in tools}, "save level", 10) == ["save_current_level"]


def test_a_new_catalog_revision_requests_a_tool_reindex(monkeypatch) -> None:
    from backend.search import indexer

    calls: list[str] = []
    monkeypatch.setattr(indexer, "request_refresh", lambda reason="": calls.append(reason))
    monkeypatch.setattr(ducky_panel, "_seen_catalog_revisions", {})
    ducky_panel._note_catalog_revision("r1")  # first sight in this process: nothing changed yet
    ducky_panel._note_catalog_revision("r1")
    ducky_panel._note_catalog_revision("r2")
    assert calls == ["tools"]


def test_chats_with_different_tool_filters_do_not_look_like_a_catalog_change(monkeypatch) -> None:
    # Two chats in one process see differently filtered catalogs; alternating between
    # them used to start a full tool re-list on every call.
    from backend.search import indexer
    from backend.uefn_plugins.plugin_host.state import _ACTIVE_UEFN_AGENT_IDS

    calls: list[str] = []
    monkeypatch.setattr(indexer, "request_refresh", lambda reason="": calls.append(reason))
    monkeypatch.setattr(ducky_panel, "_seen_catalog_revisions", {})
    for _ in range(3):
        token = _ACTIVE_UEFN_AGENT_IDS.set(("meshy",))
        ducky_panel._note_catalog_revision("only-meshy")
        _ACTIVE_UEFN_AGENT_IDS.reset(token)
        ducky_panel._note_catalog_revision("everything")
    assert calls == []
    ducky_panel._note_catalog_revision("everything-plus-a-new-plugin")
    assert calls == ["tools"]


def _get_tools_catalog(monkeypatch, *names: str) -> None:
    from backend.agent import tools as agent_tools
    from backend.search import query

    async def catalog():
        return _tools(*names)

    monkeypatch.setattr(agent_tools, "list_mcp_tools", catalog)
    monkeypatch.setattr(query, "search_tools", lambda *a, **k: [])
    monkeypatch.setattr(ducky_panel, "_discovery_miss_context", lambda: {})


def test_a_name_miss_answers_ok_with_close_matches(monkeypatch) -> None:
    # Oct 31 2026: 34 ducky_get_tools "failures" were lookups that answered {"ok": false}.
    import asyncio
    import json

    from backend.bridge.shared_mcp import _refused

    _get_tools_catalog(monkeypatch, "save_current_level", "spawn_actor")
    text = asyncio.run(ducky_panel.ducky_get_tools(name="save_levle_now"))
    data = json.loads(text)
    assert data["found"] is False and "ok" not in data
    assert "close_matches" in data and data["hint"]
    assert not _refused({"content": [{"type": "text", "text": text}]})


def test_call_tool_forwards_ducky_get_tools_instead_of_refusing(monkeypatch) -> None:
    import asyncio
    import json

    _get_tools_catalog(monkeypatch, "save_current_level")
    text = asyncio.run(ducky_panel.ducky_call_tool(name="ducky_get_tools", arguments={"name": "nope_tool"}))
    assert json.loads(text)["found"] is False


def test_pattern_paging_reaches_every_word_match(monkeypatch) -> None:
    from backend.search import query

    names = [f"thing_export_{i:03d}" for i in range(140)]
    tools = _tools(*names)
    monkeypatch.setattr(query, "search_tools", lambda *a, **k: [])
    ranked = ducky_panel._ranked_tool_names(tools, {t.name: t for t in tools}, "thing export", 50, all_words=True)
    assert sorted(ranked) == names  # every match, not the first 50
    assert len(ducky_panel._ranked_tool_names(tools, {t.name: t for t in tools}, "thing export", 50)) == 50
