"""When a desktop plugin is enabled, skills/MCP tools follow for current chats."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.mark.parametrize("backend", ["db", "files"])
def test_opt_in_uefn_plugin_all_chats(tmp_path: Path, monkeypatch, backend: str) -> None:
    """Runs on both store backends (ADR 0003): rows in ducky.db, or the legacy tree."""
    from frontend.chat_store import Conversation
    from frontend.ui_web import project_chats as pc

    monkeypatch.setenv("DUCKY_STORE_BACKEND", backend)
    explicit = Conversation(id="c1", title="t", uefn_plugins=["discord"],
                            prompt_cache_snapshot={"tool_names": ["x"]}).to_dict()
    follow = Conversation(id="c2", title="t2", uefn_plugins=None).to_dict()

    if backend == "db":
        from backend.store.repos import chats as repo

        repo.conv_save("proj_abc", explicit, messages=[])
        repo.conv_save("proj_abc", follow, messages=[])

        def read(conv_id: str) -> dict:
            return repo.conv_get(conv_id, project_id="proj_abc", with_messages=False)
    else:
        monkeypatch.setattr(pc, "_chats_root", lambda: tmp_path / "chats" / "projects")
        root = tmp_path / "chats" / "projects" / "proj_abc" / "conversations"
        for doc in (explicit, follow):
            (root / doc["id"]).mkdir(parents=True)
            (root / doc["id"] / "conversation.json").write_text(json.dumps(doc), encoding="utf-8")

        def read(conv_id: str) -> dict:
            return json.loads((root / conv_id / "conversation.json").read_text(encoding="utf-8"))

    n = pc.opt_in_uefn_plugin_all_chats("materials")
    assert n == 1
    data = read("c1")
    assert data["uefn_plugins"] == ["discord", "materials"]
    assert data["prompt_cache_snapshot"] is None
    assert read("c2").get("uefn_plugins") is None


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
