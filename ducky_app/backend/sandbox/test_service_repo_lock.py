"""sandbox_run never waits on another chat's run, not even a test run of the same repo."""

from __future__ import annotations

import threading
import time

from backend.sandbox import gitops, paths, service


class _Mgr:
    """A terminal manager whose runs stay open until ``gate`` opens."""

    def __init__(self, gate: threading.Event):
        self.gate = gate
        self.started: dict[str, float] = {}

    def set_push(self, _push):
        pass

    def spawn(self, **kw):
        return {"session_id": "s-" + kw["conv_id"]}

    def run_agent_command(self, session, command, **kw):
        self.started[kw["conv_id"]] = time.monotonic()
        self.gate.wait(5)
        return {"ok": True, "exit_code": 0}


def test_two_chats_test_the_same_repo_at_once(monkeypatch, tmp_path):
    gate = threading.Event()
    mgr = _Mgr(gate)
    monkeypatch.setattr("frontend.ui_web.terminal.get_terminal_manager", lambda: mgr)
    monkeypatch.setattr("frontend.ui_web.agent_turns.push.get_panel_push", lambda: None)
    repo = tmp_path / "repo"
    repo.mkdir()
    chats = ("chat-nolock-1", "chat-nolock-2")
    for conv in chats:
        box = paths.sandbox_dir(conv, create=True)
        gitops._save_records(box, [{"repo": str(repo), "path": str(box / "work" / "wt")}])
    out: dict = {}
    runs = [threading.Thread(target=lambda c=c: out.__setitem__(c, service.run_command(c, "py -3 -m pytest")))
            for c in chats]
    for t in runs:
        t.start()
    end = time.monotonic() + 3
    while time.monotonic() < end and len(mgr.started) < 2:
        time.sleep(0.01)
    assert len(mgr.started) == 2  # both run while the other is still running
    assert abs(mgr.started[chats[0]] - mgr.started[chats[1]]) < 1.0
    gate.set()
    for t in runs:
        t.join(5)
    for conv in chats:
        assert out[conv]["ok"] is True and "waited" not in out[conv]
