"""When a desktop plugin is enabled, skills/MCP tools follow for current chats."""

from __future__ import annotations

import json
from pathlib import Path


def test_opt_in_uefn_plugin_all_chats(tmp_path: Path, monkeypatch) -> None:
    from frontend.ui_web import project_chats as pc

    monkeypatch.setattr(pc, "_chats_root", lambda: tmp_path / "chats" / "projects")

    meta = (
        tmp_path
        / "chats"
        / "projects"
        / "proj_abc"
        / "conversations"
        / "c1"
        / "conversation.json"
    )
    meta.parent.mkdir(parents=True)
    meta.write_text(
        json.dumps(
            {
                "id": "c1",
                "title": "t",
                "uefn_plugins": ["discord"],
                "prompt_cache_snapshot": {"tool_names": ["x"]},
            }
        ),
        encoding="utf-8",
    )
    follow = (
        tmp_path
        / "chats"
        / "projects"
        / "proj_abc"
        / "conversations"
        / "c2"
        / "conversation.json"
    )
    follow.parent.mkdir(parents=True)
    follow.write_text(
        json.dumps({"id": "c2", "title": "t2", "uefn_plugins": None}),
        encoding="utf-8",
    )

    n = pc.opt_in_uefn_plugin_all_chats("materials")
    assert n == 1
    data = json.loads(meta.read_text(encoding="utf-8"))
    assert data["uefn_plugins"] == ["discord", "materials"]
    assert data["prompt_cache_snapshot"] is None
    assert json.loads(follow.read_text(encoding="utf-8")).get("uefn_plugins") is None


def test_disabled_tool_ids_leave_uefn_unscoped(monkeypatch) -> None:
    from frontend.chat_store import Conversation
    from frontend.ui_web import project_chats as pc

    monkeypatch.setattr(pc, "all_available_tool_ids", lambda: ["uefn", "materials"])
    monkeypatch.setattr(
        "backend.uefn_plugins.host.uefn_agent_tool_rows",
        lambda: [{"id": "materials"}],
    )
    # Only "materials" is an installed desktop plugin in this scenario — don't let
    # the host consult the real machine's plugin state.
    monkeypatch.setattr(
        "backend.uefn_plugins.host.is_uefn_agent_tool_plugin",
        lambda pid: pid == "materials",
    )
    conv = Conversation(id="x")
    pc._apply_disabled_tool_ids_to_conv(conv, [])
    assert conv.uefn_plugins is None

    pc._apply_disabled_tool_ids_to_conv(conv, ["materials"])
    assert conv.uefn_plugins == []
