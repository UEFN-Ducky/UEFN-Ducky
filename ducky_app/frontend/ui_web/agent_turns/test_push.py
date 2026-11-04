"""agent_turns.push: where UI events go, in the panel and in the MCP bridge."""

from __future__ import annotations

from frontend.ui_web import agent_modes
from frontend.ui_web.agent_turns import push


def test_events_go_to_the_given_push_then_the_panel_then_the_bridge_forwarder(monkeypatch):
    monkeypatch.setattr(push, "_panel_push", None)
    mine: list[dict] = []
    assert push._resolve_push(mine.append) == mine.append
    assert push._resolve_push(None) is push._forward_to_panel
    assert push._in_bridge_process()
    panel: list[dict] = []
    push.set_panel_push(panel.append)
    assert push.get_panel_push() == panel.append and not push._in_bridge_process()
    push.push_ui_event({"type": "x"})
    assert panel == [{"type": "x"}]


def test_chats_changed_names_the_chat_and_what_was_removed():
    seen: list[dict] = []
    push.notify_chats_changed("c1", "Title", "f1", push=seen.append, open_tab=False,
                              removed_conv_ids=["a", ""], removed_folder_ids=["f"])
    assert seen == [{"type": "chats_changed", "conv_id": "c1", "title": "Title", "folder_id": "f1",
                     "open": False, "removed_conv_ids": ["a"], "removed_folder_ids": ["f"]}]
    push.notify_context_changed("c1", push=seen.append)
    assert seen[-1] == {"type": "context_changed", "conv_id": "c1"}


def test_the_active_chat_is_per_thread():
    push._set_active_conv_id("c9")
    assert push.get_active_conv_id() == "c9"
    push._set_active_conv_id(None)
    assert push.get_active_conv_id() is None


def test_the_old_path_reads_and_patches_the_moved_code(monkeypatch):
    assert agent_modes.notify_chats_changed is push.notify_chats_changed
    monkeypatch.setattr(agent_modes, "_panel_push", print)  # a name rebound in push itself
    assert push._panel_push is print and agent_modes._panel_push is print
    monkeypatch.setattr(agent_modes, "_resolve_push", lambda _p: None)
    assert push._resolve_push(None) is None
    monkeypatch.undo()
    assert push._resolve_push is agent_modes._resolve_push and push._panel_push is not print
