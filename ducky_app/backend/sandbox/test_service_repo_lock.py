"""sandbox_run: one test or build run per source repo, held until the program really ends."""

from __future__ import annotations

import threading
import time
import types

from backend.sandbox import gitops, paths, service
from frontend.ui_web.terminal import repo_lock


class _Mgr:
    """A terminal manager whose runs end when their chat's gate opens."""

    def __init__(self, gates=None, answer=None, busy=None):
        self.gates, self.answer, self.busy = gates or {}, answer, busy
        self.started: list[str] = []

    def set_push(self, _push):
        pass

    def spawn(self, **kw):
        return {"session_id": "s-" + kw["conv_id"]}

    def run_agent_command(self, session, command, **kw):
        self.started.append(kw["conv_id"])
        if self.answer is not None:
            return dict(self.answer)
        gate = self.gates.get(kw["conv_id"])
        if gate is not None:
            gate.wait(5)
        return {"ok": True, "exit_code": 0}

    def get_session(self, _sid):
        return types.SimpleNamespace(is_busy=lambda: self.busy.is_set())


def _until(check, secs=5.0):
    end = time.monotonic() + secs
    while time.monotonic() < end and not check():
        time.sleep(0.01)
    assert check()


def _boxes(monkeypatch, mgr, repos):
    monkeypatch.setattr("frontend.ui_web.terminal.get_terminal_manager", lambda: mgr)
    monkeypatch.setattr("frontend.ui_web.agent_turns.push.get_panel_push", lambda: None)
    for conv, repo in repos.items():
        repo.mkdir(exist_ok=True)
        box = paths.sandbox_dir(conv, create=True)
        gitops._save_records(box, [{"repo": str(repo), "path": str(box / "work" / "wt")}])


def test_test_runs_of_one_source_repo_take_turns(monkeypatch, tmp_path):
    repos = {"chat-lock-1": tmp_path / "a", "chat-lock-2": tmp_path / "a", "chat-lock-3": tmp_path / "b"}
    gates = {c: threading.Event() for c in repos}
    mgr = _Mgr(gates)
    _boxes(monkeypatch, mgr, repos)
    out: dict = {}
    runs = {c: threading.Thread(target=lambda c=c: out.__setitem__(c, service.run_command(c, "pytest -q")))
            for c in repos}
    runs["chat-lock-1"].start()
    _until(lambda: mgr.started == ["chat-lock-1"])
    runs["chat-lock-2"].start()
    runs["chat-lock-3"].start()
    _until(lambda: "chat-lock-3" in mgr.started)  # another repo runs at once
    time.sleep(0.2)
    assert "chat-lock-2" not in mgr.started  # the same repo waits
    gates["chat-lock-1"].set()
    _until(lambda: "chat-lock-2" in mgr.started)
    for gate in gates.values():
        gate.set()
    for t in runs.values():
        t.join(5)
    assert "test run in this repo" in out["chat-lock-2"]["waited"]
    assert "waited" not in out["chat-lock-1"] and "waited" not in out["chat-lock-3"]
    assert not repo_lock.holder(repo_lock.repo_of(str(tmp_path / "a")))


def test_other_commands_are_never_locked(monkeypatch, tmp_path):
    mgr = _Mgr()
    _boxes(monkeypatch, mgr, {"chat-lock-5": tmp_path / "d"})
    key = repo_lock.repo_of(str(tmp_path / "d"))
    repo_lock.acquire(key, "someone")
    try:
        assert service.run_command("chat-lock-5", "npm ci")["ok"] is True  # no wait on a held repo
    finally:
        repo_lock.release(key)


def test_an_early_returned_run_holds_the_repo_until_it_ends(monkeypatch, tmp_path):
    monkeypatch.setattr(service, "_LOCK_POLL_S", 0.01)
    busy = threading.Event()
    busy.set()
    mgr = _Mgr(answer={"ok": True, "still_running": True, "session_id": "s"}, busy=busy)
    _boxes(monkeypatch, mgr, {"chat-lock-4": tmp_path / "c"})
    out = service.run_command("chat-lock-4", "pytest")
    key = repo_lock.repo_of(str(tmp_path / "c"))
    assert out["still_running"] is True and repo_lock.holder(key)  # the program still runs
    busy.clear()
    _until(lambda: not repo_lock.holder(key))
