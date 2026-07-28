"""Follow Code: writes open files; reads never open tabs; bridge tools emit editor_batch."""

from __future__ import annotations

from typing import Any

import frontend.ui_web.verse_editor.agent_sync as agent_sync


def test_normalize_workspace_tool_name_strips_mcp_prefix() -> None:
    assert agent_sync.normalize_workspace_tool_name("mcp__uefn__workspace_write_file") == "workspace_write_file"
    assert agent_sync.normalize_workspace_tool_name("workspace_read_file") == "workspace_read_file"


def test_build_editor_batch_read_does_not_open_tab(monkeypatch) -> None:
    """workspace_read_file / Read warm cache only — never open a Follow Code tab."""
    monkeypatch.setattr(agent_sync, "verse_editor_enabled", lambda: True)
    reads: list[str] = []

    def _read(path: str) -> dict[str, str]:
        reads.append(path)
        return {"path": path, "content": "using { /Fortnite.com/Devices }\n"}

    monkeypatch.setattr(agent_sync.io, "read_file", _read)
    batch = agent_sync.build_editor_batch(
        "conv-1",
        "workspace_read_file",
        {"relative_path": "Content/Verse/Demo.verse"},
        {"data": {"content": "using { /Fortnite.com/Devices }\n"}},
    )
    assert batch is None
    assert reads == ["Content/Verse/Demo.verse"]

    reads.clear()
    batch_alias = agent_sync.build_editor_batch(
        "conv-1",
        "Read",
        {"path": "Content/Verse/Other.verse"},
        {},
    )
    assert batch_alias is None
    assert reads == ["Content/Verse/Other.verse"]


def test_build_editor_batch_write_starts_with_open_file(monkeypatch) -> None:
    monkeypatch.setattr(agent_sync, "verse_editor_enabled", lambda: True)
    monkeypatch.setattr(agent_sync.io, "seed_cache", lambda *_a, **_k: None)
    batch = agent_sync.build_editor_batch(
        "conv-1",
        "workspace_write_file",
        {
            "relative_path": "Content/Verse/Demo.verse",
            "content": "using { /Fortnite.com/Devices }\n# after\n",
        },
        {
            "data": {
                "before_content": "using { /Fortnite.com/Devices }\n",
                "lines_added": 1,
                "lines_removed": 0,
            }
        },
    )
    assert batch is not None
    assert batch.actions
    assert batch.actions[0].type == "open_file"
    assert batch.actions[0].path == "Content/Verse/Demo.verse"
    assert batch.actions[0].activate is False
    assert any(a.type == "apply_content" for a in batch.actions)


def test_build_editor_batch_unchanged_write_skips_open(monkeypatch) -> None:
    monkeypatch.setattr(agent_sync, "verse_editor_enabled", lambda: True)
    monkeypatch.setattr(agent_sync.io, "seed_cache", lambda *_a, **_k: None)
    text = "using { /Fortnite.com/Devices }\n"
    batch = agent_sync.build_editor_batch(
        "conv-1",
        "workspace_write_file",
        {"relative_path": "Content/Verse/Demo.verse", "content": text},
        {"data": {"before_content": text}},
    )
    assert batch is not None
    assert [a.type for a in batch.actions] == ["apply_content"]


def test_emit_for_bridge_tool_pushes_when_no_panel(monkeypatch) -> None:
    monkeypatch.setattr(agent_sync, "verse_editor_enabled", lambda: True)
    monkeypatch.setattr(agent_sync.io, "seed_cache", lambda *_a, **_k: None)
    pushed: list[dict[str, Any]] = []

    monkeypatch.setattr(
        "frontend.ui_web.agent_modes.get_panel_push",
        lambda: None,
    )
    monkeypatch.setattr(
        "frontend.ui_web.verse_editor.panel_events.push_agent_event",
        pushed.append,
    )

    agent_sync.emit_for_bridge_tool(
        "workspace_write_file",
        {
            "relative_path": "Content/Verse/Demo.verse",
            "content": "using { /Fortnite.com/Devices }\n# bridge\n",
        },
        {
            "data": {
                "before_content": "using { /Fortnite.com/Devices }\n",
                "relative_path": "Content/Verse/Demo.verse",
            }
        },
        conv_id="conv-bridge",
    )

    types = [e.get("type") for e in pushed]
    assert "editor_batch" in types
    assert "file_sync" in types
    batch_evt = next(e for e in pushed if e["type"] == "editor_batch")
    assert batch_evt["conv_id"] == "conv-bridge"
    actions = batch_evt["editor_batch"]["actions"]
    assert actions[0]["type"] == "open_file"


def test_emit_for_bridge_tool_noop_when_panel_owns_push(monkeypatch) -> None:
    pushed: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "frontend.ui_web.agent_modes.get_panel_push",
        lambda: (lambda _e: None),
    )
    monkeypatch.setattr(
        "frontend.ui_web.verse_editor.panel_events.push_agent_event",
        pushed.append,
    )
    agent_sync.emit_for_bridge_tool(
        "workspace_write_file",
        {"relative_path": "Content/Verse/Demo.verse", "content": "x\n"},
        {"data": {"before_content": ""}},
    )
    assert pushed == []


def test_file_edit_meta_for_stream_mcp_name(monkeypatch) -> None:
    monkeypatch.setattr(agent_sync, "_before_from_history", lambda _p: "old\n")
    meta = agent_sync.file_edit_meta_for_stream(
        "mcp__uefn__workspace_write_file",
        {"relative_path": "Content/Verse/Demo.verse", "content": "new\n"},
        '{"before_content":"old\\n","lines_added":1,"lines_removed":1}',
    )
    assert meta is not None
    assert meta["path"] == "Content/Verse/Demo.verse"
    assert meta["before"] == "old\n"
    assert meta["after"] == "new\n"
    assert meta["kind"] == "write"


def test_file_edit_meta_cursor_edit_search_replace(monkeypatch) -> None:
    monkeypatch.setattr(agent_sync.io, "get_cached", lambda _p: None)
    monkeypatch.setattr(
        agent_sync.io,
        "read_file",
        lambda _p: {"path": "Content/Verse/Demo.verse", "content": "hello world\n"},
    )
    monkeypatch.setattr(agent_sync, "_before_from_history", lambda _p: "")
    meta = agent_sync.build_file_edit_meta(
        "edit",
        {
            "path": "Content/Verse/Demo.verse",
            "old_string": "hello",
            "new_string": "hello world",
        },
        {},
    )
    assert meta is not None
    assert meta["before"] == "hello\n"
    assert meta["after"] == "hello world\n"
    assert meta["linesAdded"] >= 0


def test_file_edit_meta_cursor_edit_uses_seeded_cache(monkeypatch) -> None:
    monkeypatch.setattr(agent_sync.io, "get_cached", lambda _p: "before line\n")
    monkeypatch.setattr(
        agent_sync.io,
        "read_file",
        lambda _p: {"path": "Content/Verse/Demo.verse", "content": "after line\n"},
    )
    monkeypatch.setattr(agent_sync, "_before_from_history", lambda _p: "")
    meta = agent_sync.build_file_edit_meta(
        "Edit",
        {"path": "Content/Verse/Demo.verse"},
        {},
    )
    assert meta is not None
    assert meta["before"] == "before line\n"
    assert meta["after"] == "after line\n"
