"""Checkpoints are written in the host; the app hears `saved` and reloads only chats on screen."""

from __future__ import annotations

import os
import sys
import threading
import uuid
from types import SimpleNamespace

from frontend.agent_host import protocol, turns


def test_checkpoints_save_in_the_host_and_tell_the_app(monkeypatch):
    from backend.agent.coding_agents import runner
    from frontend import settings
    from frontend.ui_web.agent_turns.testing import patch_turns
    from frontend.ui_web.chats import conversations as chat_convs, saves as chat_saves

    monkeypatch.setattr(runner, "on_saved", None)
    from frontend.agent_host import steer_host

    monkeypatch.setattr(steer_host, "_in_host", False)
    saves: list[tuple[int, str]] = []
    monkeypatch.setattr(chat_saves, "upsert_in_flight_assistant",
                        lambda conv, msg, **kw: saves.append((threading.get_ident(), msg["run_id"])))
    monkeypatch.setattr(settings, "PanelSettings", SimpleNamespace(load=lambda: SimpleNamespace(uefn_project_root="")))
    monkeypatch.setattr(settings, "apply_workspace_env", lambda root: None)
    monkeypatch.setattr(chat_convs, "load_conversation", lambda cid: SimpleNamespace(id=cid, messages=[]))

    def stub(conv, text, *, push, run_id, **kw):
        runner.checkpoint_coding_turn(conv, agent_id="claude", run_id=run_id, blocks=[])
        runner._notify_saved(conv.id, final=True)
        push({"type": "agent_stopped", "reason": "done"})

    monkeypatch.setattr(runner, "run_coding_agent_message", stub)
    reloads: list[str] = []
    patch_turns(monkeypatch, "notify_context_changed", lambda cid, **kw: reloads.append(cid))
    monkeypatch.setattr(turns, "_sender", lambda ch: lambda ev: ch.send("event", conv_id=ev["conv_id"], events=[ev]))

    tag = f"t-{uuid.uuid4().hex[:8]}"
    key = os.urandom(16)
    listener = protocol.listen(tag, key)
    box = []
    t = threading.Thread(target=lambda: box.append(protocol.connect_app(
        protocol.pipe_name(tag), key, lambda frame: turns.on_frame(box[0], frame), heartbeat_s=0)), daemon=True)
    t.start()
    frames: list[dict] = []
    holder = {}

    def on_app(frame):
        frames.append(frame)
        turns.on_app_frame(holder["host"], frame)

    app = protocol.accept_host(listener, on_app, dead_after_s=0)
    t.join(5)
    listener.close()
    host = holder["host"] = SimpleNamespace(ready=threading.Event(), alive=lambda: not app.closed,
                                            send=lambda op, **f: app.send(op, **f))
    host.ready.set()
    monkeypatch.setattr(turns, "_supervisor", lambda: SimpleNamespace(assign=lambda cid: host, release=lambda cid: None))
    try:
        main_thread = threading.get_ident()
        assert turns.run_in_host("c1", "r1", "hi", model="m", mode="agent", push=lambda e: None,
                                 cancel=threading.Event()) == ""
        assert saves and all(tid != main_thread for tid, _ in saves)  # saved on the host's turn thread
        saved = [f for f in frames if f["op"] == "saved"]
        assert saved == [{"op": "saved", "conv_id": "c1", "final": False},
                         {"op": "saved", "conv_id": "c1", "final": True}]
        assert reloads == ["c1"]  # the final save reloads; mid-turn checkpoints don't
    finally:
        app.close()
        box[0].close()


def test_saved_reloads_only_chats_on_screen(monkeypatch):
    from frontend.ui_web.agent_turns.testing import patch_turns

    reloads: list[str] = []
    patch_turns(monkeypatch, "notify_context_changed", lambda cid, **kw: reloads.append(cid))
    panel = SimpleNamespace(_visible_by_viewer={"main": {"shown"}}, _visible_union=lambda: {"shown"})
    monkeypatch.setitem(sys.modules, "frontend.ui_web.panel_api", panel)
    for conv_id in ("shown", "hidden"):
        turns.on_app_frame(None, {"op": "saved", "conv_id": conv_id, "final": True})
    turns.on_app_frame(None, {"op": "saved", "conv_id": "shown", "final": False})
    assert reloads == ["shown"]
    panel._visible_by_viewer = {}  # no window reported yet: every chat counts as shown
    turns.on_app_frame(None, {"op": "saved", "conv_id": "hidden", "final": True})
    assert reloads == ["shown", "hidden"]
