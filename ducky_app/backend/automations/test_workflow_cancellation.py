"""Stop crosses process boundaries, kills owned commands, and leaves other runs alone."""
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import psutil
import pytest

from backend.automations import live_runs, runner, store


def until(predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "Timed out waiting for test condition"
        time.sleep(0.02)


@pytest.fixture
def local_runs(monkeypatch, tmp_path):
    monkeypatch.setattr(live_runs, "_directory", lambda: tmp_path / "runs")
    monkeypatch.setattr(store, "use_db", lambda *_: False)
    monkeypatch.setattr(store, "_files_dir", lambda: tmp_path / "graphs")
    monkeypatch.setattr(store, "_announce_graphs_changed", lambda: None)
    monkeypatch.setattr(runner, "_notify_phone", lambda *_: None)
    return tmp_path


def workflow():
    return store.save_workflow({"name": "Concurrent", "graph": {
        "nodes": [
            {"id": "start", "type": "start.manual", "config": {}},
            {"id": "wait", "type": "flow.wait", "config": {"seconds": 60}},
            {"id": "end", "type": "flow.end", "config": {}},
        ],
        "edges": [{"source": "start", "target": "wait", "kind": "main"},
                  {"source": "wait", "target": "end", "kind": "main"}],
    }})["id"]


def test_stop_reaches_another_process(local_runs):
    wid = workflow()
    script = """
import json, sys
from pathlib import Path
from backend.automations import live_runs, runner, store
root = Path(sys.argv[1])
live_runs._directory = lambda: root / "runs"
store.use_db = lambda *_: False
store._files_dir = lambda: root / "graphs"
runner._notify_phone = lambda *_: None
print(json.dumps(runner.run_workflow(sys.argv[2])), flush=True)
"""
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[2]))
    child = subprocess.Popen([sys.executable, "-c", script, str(local_runs), wid],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
    try:
        until(lambda: runner.is_running(wid))
        assert not runner._ACTIVE.get(wid), "Parent must not own the child's run"
        assert runner.stop_workflow(wid)
        output, errors = child.communicate(timeout=8)
        assert child.returncode == 0, errors
        result = json.loads(output.strip().splitlines()[-1])
        assert not result["ok"] and result["error"] == runner.STOPPED
        assert not runner.is_running(wid)
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()


def test_stop_one_of_two_concurrent_runs(local_runs, monkeypatch):
    events = []
    monkeypatch.setattr("frontend.ui_web.agent_modes.push_ui_event", events.append)
    wid = workflow()
    results = [{}, {}]
    workers = [threading.Thread(target=lambda result=result: result.update(runner.run_workflow(wid)))
               for result in results]
    for worker in workers:
        worker.start()
    try:
        until(lambda: len([e for e in events if e["type"] == "workflow_run" and e["state"] == "started"]) == 2)
        ids = [e["run"] for e in events if e["type"] == "workflow_run" and e["state"] == "started"]
        assert runner.stop_workflow(wid, ids[0])
        until(lambda: any(e["type"] == "workflow_run" and e["run"] == ids[0] and e["state"] == "stopped" for e in events))
        assert runner.is_running(wid)
        assert not live_runs.cancelled(wid, ids[1])
        assert not any(e["type"] == "workflow_run" and e["run"] == ids[1] and e["state"] != "started" for e in events)
        assert runner.stop_workflow(wid, ids[1])
    finally:
        runner.stop_workflow(wid)
        for worker in workers:
            worker.join(5)
    assert not any(worker.is_alive() for worker in workers)
    assert all(result["error"] == runner.STOPPED for result in results)
    assert all(not any(step["id"] == "end" for step in result["steps"]) for result in results)
    activity_ids = [e["id"] for e in events if e["type"] == "background_job" and e["phase"] == "working"]
    assert len(set(activity_ids)) == 2


def test_dead_owner_is_reported_stopped(local_runs):
    live_runs.start("wf", "dead")
    live_runs.record({"type": "workflow_run", "id": "wf", "run": "dead", "state": "started"})
    path = live_runs._path("wf", "dead")
    data = json.loads(path.read_text())
    data["process_started"] = -1  # PID reused or owner exited
    path.write_text(json.dumps(data))
    assert not live_runs.running("wf")
    assert live_runs.snapshot()[-1]["state"] == "stopped"


@pytest.mark.skipif(sys.platform != "win32", reason="Ducky's Windows ConPTY terminal")
def test_stop_kills_active_terminal_and_child(local_runs, monkeypatch):
    from backend.server import mcp
    from frontend.ui_web.terminal import manager as terminal_module
    mgr = terminal_module.TerminalManager()
    monkeypatch.setattr(terminal_module, "get_terminal_manager", lambda: mgr)
    terminal = mgr.spawn(shell="powershell", cwd=str(local_runs), hidden=True)
    assert terminal["ok"], terminal
    sid = terminal["session_id"]
    pid_file = local_runs / "child.pid"
    # This long-lived Python child is inside the workflow's actual terminal tree.
    code = "import os,time;from pathlib import Path;Path('child.pid').write_text(str(os.getpid()));time.sleep(60)"
    command = "& '" + sys.executable.replace("'", "''") + "' -c '" + code.replace("'", "''") + "'"
    def run_command(**arguments):
        return mgr.run_agent_command(sid, command, auto_approve=True, command_timeout_s=90)
    monkeypatch.setattr(mcp._tool_manager, "get_tool", lambda _: SimpleNamespace(fn=run_command))
    wid = store.save_workflow({"name": "Terminal stop", "graph": {
        "nodes": [{"id": "command", "type": "tool.call",
                   "config": {"name": "ducky_terminal_run", "arguments": {"session_id": sid, "command": command}}},
                  {"id": "next", "type": "flow.end", "config": {}}],
        "edges": [{"source": "command", "target": "next", "kind": "main"}],
    }})["id"]
    result = {}
    worker = threading.Thread(target=lambda: result.update(runner.run_workflow(wid)))
    worker.start()
    child_pid = None
    try:
        until(pid_file.exists)
        child_pid = int(pid_file.read_text())
        assert psutil.pid_exists(child_pid)
        assert runner.stop_workflow(wid)
        worker.join(8)
        assert not worker.is_alive()
        assert result["error"] == runner.STOPPED
        until(lambda: not psutil.pid_exists(child_pid), timeout=5)
        assert mgr.get_session(sid) is None
        assert not any(step["id"] == "next" for step in result["steps"])
    finally:
        runner.stop_workflow(wid)
        mgr.kill(sid)
        worker.join(8)
        if child_pid and psutil.pid_exists(child_pid):
            psutil.Process(child_pid).kill()
